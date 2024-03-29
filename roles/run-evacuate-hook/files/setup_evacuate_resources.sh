#!/bin/bash
source /opt/stack/devstack/openrc admin
set -x
set -e

image_id=$(openstack image list -f value -c ID | awk 'NR==1{print $1}')
flavor_id=$(openstack flavor list -f value -c ID | awk 'NR==1{print $1}')
network_id=$(openstack network list --no-share -f value -c ID | awk 'NR==1{print $1}')

echo "Creating ephemeral test server on subnode"
openstack --os-compute-api-version 2.74 server create --image ${image_id} --flavor ${flavor_id} \
--nic net-id=${network_id} --host $SUBNODE_HOSTNAME --wait evacuate-test

if openstack endpoint list | grep cinder; then
    echo "Creating boot from volume test server on subnode"
    openstack --os-compute-api-version 2.74  server create --flavor ${flavor_id} \
    --block-device source_type=image,uuid=${image_id},destination_type=volume,volume_size=1,boot_index=0,delete_on_termination=true \
    --network ${network_id} --host ${SUBNODE_HOSTNAME} --wait evacuate-bfv-test
fi

echo "Forcing down the subnode so we can evacuate from it"
openstack --os-compute-api-version 2.11 compute service set --down ${SUBNODE_HOSTNAME} nova-compute

count=0
status=$(openstack compute service list --host ${SUBNODE_HOSTNAME} --service nova-compute -f value -c State)
while [ "${status}" != "down" ]
do
    sleep 1
    count=$((count+1))
    if [ ${count} -eq 30 ]; then
        echo "Timed out waiting for subnode compute service to be marked as down"
        exit 5
    fi
    status=$(openstack compute service list --host ${SUBNODE_HOSTNAME} --service nova-compute -f value -c State)
done
