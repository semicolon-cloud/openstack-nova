#!/bin/bash -x

MANAGE="/usr/local/bin/nova-manage"

function archive_deleted_rows {
    # NOTE(danms): Run this a few times to make sure that we end
    # up with nothing more to archive
    if ! $MANAGE db archive_deleted_rows --verbose --before "$(date -d yesterday)" 2>&1 | grep 'Nothing was archived'; then
        echo "Archiving yesterday data should have done nothing"
        return 1
    fi
    for i in `seq 30`; do
        if [[ $i -eq 1 ]]; then
            # This is just a test wrinkle to make sure we're covering the
            # non-all-cells (cell0) case, as we're not passing in the cell1
            # config.
            $MANAGE db archive_deleted_rows --verbose --max_rows 50 --before "$(date -d tomorrow)"
        else
            $MANAGE db archive_deleted_rows --verbose --max_rows 1000 --before "$(date -d tomorrow)" --all-cells
        fi
        RET=$?
        if [[ $RET -gt 1 ]]; then
            echo Archiving failed with result $RET
            return $RET
        # When i = 1, we only archive cell0 (without --all-cells), so run at
        # least twice to ensure --all-cells are archived before considering
        # archiving complete.
        elif [[ $RET -eq 0 && $i -gt 1 ]]; then
            echo Archiving Complete
            break;
        fi
    done
}

function purge_db {
    $MANAGE db purge --all --verbose --all-cells
    RET=$?
    if [[ $RET -eq 0 ]]; then
        echo Purge successful
    else
        echo Purge failed with result $RET
        return $RET
    fi
}

BASE=${BASE:-/opt/stack}
source ${BASE}/devstack/functions-common
source ${BASE}/devstack/lib/nova

# This needs to go before 'set -e' because otherwise the intermediate runs of
# 'nova-manage db archive_deleted_rows' returning 1 (normal and expected) would
# cause this script to exit and fail.
archive_deleted_rows

set -e

# This needs to go after 'set -e' because otherwise a failure to purge the
# database would not cause this script to exit and fail.
purge_db

# We need to get the admin credentials to run the OSC CLIs for Placement.
export OS_CLOUD=devstack-admin

# Verify whether instances were archived from all cells. Admin credentials are
# needed to list deleted instances across all projects.
echo "Verifying that instances were archived from all cells"
deleted_servers=$(openstack server list --deleted --all-projects -c ID -f value)

# Fail if any deleted servers were found.
if [[ -n "$deleted_servers" ]]; then
    echo "There were unarchived instances found after archiving; failing."
    exit 1
fi

# TODO(mriedem): Consider checking for instances in ERROR state because
# if there are any, we would expect them to retain allocations in Placement
# and therefore we don't really need to check for leaked allocations.

# Check for orphaned instance allocations in Placement which could mean
# something failed during a test run and isn't getting cleaned up properly.
echo "Looking for leaked resource provider allocations in Placement"
LEAKED_ALLOCATIONS=0
for provider in $(openstack resource provider list -c uuid -f value); do
    echo "Looking for allocations for provider $provider"
    allocations=$(openstack resource provider show --allocations $provider \
                  -c allocations -f value)
    if [[ "$allocations" != "{}" ]]; then
        echo "Resource provider has allocations:"
        openstack resource provider show --allocations $provider
        LEAKED_ALLOCATIONS=1
    fi
done

# Fail if there were any leaked allocations.
if [[ $LEAKED_ALLOCATIONS -eq 1 ]]; then
    echo "There were leaked allocations; failing."
    exit 1
fi
echo "Resource provider allocations were cleaned up properly."


# Test "nova-manage placement heal_allocations" by creating a server, deleting
# its allocations in placement, and then running heal_allocations and assert
# the allocations were healed as expected.

function get_binding_profile_value
{
    # Returns the value of the key in the binding profile if exists or return
    # empty.
    local port=${1}
    local key=${2}
    local print_value='import sys, json; print(json.load(sys.stdin).get("binding_profile", {}).get("'${key}'", ""))'
    openstack port show ${port} -f json -c binding_profile \
    | /usr/bin/env python3 -c "${print_value}"
}

echo "Creating port with bandwidth request for heal_allocations testing"
openstack network create net0 \
    --provider-network-type vlan \
    --provider-physical-network public \
    --provider-segment 100

openstack subnet create subnet0 \
    --network net0 \
    --subnet-range 10.0.4.0/24 \

openstack network qos policy create qp0
openstack network qos rule create qp0 \
    --type minimum-bandwidth \
    --min-kbps 1000 \
    --egress

openstack network qos rule create qp0 \
    --type minimum-bandwidth \
    --min-kbps 1000 \
    --ingress

openstack port create port-normal-qos \
    --network net0 \
    --vnic-type normal \
    --qos-policy qp0

image_id=$(openstack image list -f value -c ID | awk 'NR==1{print $1}')
flavor_id=$(openstack flavor list -f value -c ID | awk 'NR==1{print $1}')
network_id=$(openstack network list --no-share -f value -c ID | awk 'NR==1{print $1}')

echo "Creating server for heal_allocations testing"
# microversion 2.72 introduced the support for bandwidth aware ports
openstack --os-compute-api-version 2.72 \
server create --image ${image_id} --flavor ${flavor_id} \
--nic net-id=${network_id} --nic port-id=port-normal-qos \
--wait heal-allocations-test
server_id=$(openstack server show heal-allocations-test -f value -c id)

# Make sure there are allocations for the consumer.
allocations=$(openstack resource provider allocation show ${server_id} \
              -c resources -f value)
if [[ "$allocations" == "" ]]; then
    echo "No allocations found for the server."
    exit 2
fi

echo "Deleting allocations in placement for the server"
openstack resource provider allocation delete ${server_id}

# Make sure the allocations are gone.
allocations=$(openstack resource provider allocation show ${server_id} \
              -c resources -f value)
if [[ "$allocations" != "" ]]; then
    echo "Server allocations were not deleted."
    exit 2
fi

echo "Healing allocations"
# First test with the --dry-run over all instances in all cells.
set +e
nova-manage placement heal_allocations --verbose --dry-run
rc=$?
set -e
# Since we did not create allocations because of --dry-run the rc should be 4.
if [[ ${rc} -ne 4 ]]; then
    echo "Expected return code 4 from heal_allocations with --dry-run"
    exit 2
fi
# Now test with just the single instance and actually perform the heal.
nova-manage placement heal_allocations --verbose --instance ${server_id}

# Make sure there are allocations for the consumer.
allocations=$(openstack resource provider allocation show ${server_id} \
              -c resources -f value)
if [[ "$allocations" == "" ]]; then
    echo "Failed to heal allocations."
    exit 2
fi

echo "Verifying online_data_migrations idempotence"
# We will reuse the server created earlier for this test. (A server needs to
# be present during the run of online_data_migrations and archiving).

# Run the online data migrations before archiving.
$MANAGE db online_data_migrations

# We need to archive the deleted marker instance used by the
# fill_virtual_interface_list online data migration in order to trigger
# creation of a new deleted marker instance.
set +e
archive_deleted_rows
set -e

# Verify whether online data migrations run after archiving will succeed.
# See for more details: https://bugs.launchpad.net/nova/+bug/1824435
$MANAGE db online_data_migrations


# Test global registered unified limits by updating registered limits and
# attempting to create resources. Because these quota limits are global, we
# can't test them in tempest because modifying global limits can cause other
# tests running in parallel to fail.
echo "Testing unified limits registered limits"

# Get the registered limits IDs.
reglimit_ids_names=$(openstack registered limit list -f value -c "ID" -c "Resource Name")

# Put them in a map to lookup ID from name for subsequent limit set commands.
# Requires Bash 4.
declare -A id_name_map
while read id name
    do id_name_map["$name"]="$id"
done <<< "$reglimit_ids_names"

# Server metadata items
#
# Set the quota to 1.
metadata_items_id="${id_name_map["server_metadata_items"]}"

bash -c "unset OS_USERNAME OS_TENANT_NAME OS_PROJECT_NAME;
    openstack --os-cloud devstack-system-admin registered limit set \
        --default-limit 1 $metadata_items_id"

# Create a server. Should succeed with one metadata item.
openstack --os-compute-api-version 2.37 \
    server create --image ${image_id} --flavor ${flavor_id} --nic none \
    --property cool=true --wait metadata-items-test1

# Try to create another server with two metadata items. This should fail.
set +e
output=$(openstack --os-compute-api-version 2.37 \
    server create --image ${image_id} --flavor ${flavor_id} --nic none \
    --property cool=true --property location=fridge \
    --wait metadata-items-test2)
rc=$?
set -e
# Return code should be 1 if server create failed.
if [[ ${rc} -ne 1 ]]; then
    echo "Expected return code 1 from server create with two metadata items"
    exit 2
fi
# Verify it's a quota error.
if [[ ! "HTTP 403" =~ "$output" ]]; then
    echo "Expected HTTP 403 from server create with two metadata items"
    exit 2
fi

# Increase the quota limit to two.
bash -c "unset OS_USERNAME OS_TENANT_NAME OS_PROJECT_NAME;
    openstack --os-cloud devstack-system-admin registered limit set \
        --default-limit 2 $metadata_items_id"

# Second server create should succeed now.
openstack --os-compute-api-version 2.37 \
    server create --image ${image_id} --flavor ${flavor_id} --nic none \
    --property cool=true --property location=fridge --wait metadata-items-test2

# Delete the servers.
openstack server delete metadata-items-test1 metadata-items-test2
