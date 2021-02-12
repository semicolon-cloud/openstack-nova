# Copyright 2014 IBM Corp.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import importlib

from migrate import exceptions as versioning_exceptions
from migrate import UniqueConstraint
from migrate.versioning import api as versioning_api
import mock
from oslo_db.sqlalchemy import utils as db_utils
from oslo_utils.fixture import uuidsentinel
import sqlalchemy

from nova import context
from nova.db.sqlalchemy import api as db_api
from nova.db.sqlalchemy import migration
from nova import exception
from nova import objects
from nova import test
from nova.tests import fixtures as nova_fixtures


class TestNullInstanceUuidScanDB(test.TestCase):

    # NOTE(mriedem): Copied from the 267 database migration.
    def downgrade(self, migrate_engine):
        UniqueConstraint('uuid',
                         table=db_utils.get_table(migrate_engine, 'instances'),
                         name='uniq_instances0uuid').drop()
        for table_name in ('instances', 'shadow_instances'):
            table = db_utils.get_table(migrate_engine, table_name)
            table.columns.uuid.alter(nullable=True)

    def setUp(self):
        super(TestNullInstanceUuidScanDB, self).setUp()

        self.engine = db_api.get_engine()
        # When this test runs, we've already run the schema migration to make
        # instances.uuid non-nullable, so we have to alter the table here
        # so we can test against a real database.
        self.downgrade(self.engine)
        # Now create fake entries in the fixed_ips, consoles and
        # instances table where (instance_)uuid is None for testing.
        for table_name in ('fixed_ips', 'instances', 'consoles'):
            table = db_utils.get_table(self.engine, table_name)
            fake_record = {'id': 1}
            table.insert().execute(fake_record)

    def test_db_null_instance_uuid_scan_readonly(self):
        results = migration.db_null_instance_uuid_scan(delete=False)
        self.assertEqual(1, results.get('instances'))
        self.assertEqual(1, results.get('consoles'))
        # The fixed_ips table should be ignored.
        self.assertNotIn('fixed_ips', results)
        # Now pick a random table with an instance_uuid column and show it's
        # in the results but with 0 hits.
        self.assertEqual(0, results.get('instance_info_caches'))
        # Make sure nothing was deleted.
        for table_name in ('fixed_ips', 'instances', 'consoles'):
            table = db_utils.get_table(self.engine, table_name)
            record = table.select(table.c.id == 1).execute().first()
            self.assertIsNotNone(record)

    def test_db_null_instance_uuid_scan_delete(self):
        results = migration.db_null_instance_uuid_scan(delete=True)
        self.assertEqual(1, results.get('instances'))
        self.assertEqual(1, results.get('consoles'))
        # The fixed_ips table should be ignored.
        self.assertNotIn('fixed_ips', results)
        # Now pick a random table with an instance_uuid column and show it's
        # in the results but with 0 hits.
        self.assertEqual(0, results.get('instance_info_caches'))
        # Make sure fixed_ips wasn't touched, but instances and instance_faults
        # records were deleted.
        fixed_ips = db_utils.get_table(self.engine, 'fixed_ips')
        record = fixed_ips.select(fixed_ips.c.id == 1).execute().first()
        self.assertIsNotNone(record)

        consoles = db_utils.get_table(self.engine, 'consoles')
        record = consoles.select(consoles.c.id == 1).execute().first()
        self.assertIsNone(record)

        instances = db_utils.get_table(self.engine, 'instances')
        record = instances.select(instances.c.id == 1).execute().first()
        self.assertIsNone(record)


@mock.patch.object(migration, 'db_version', return_value=2)
@mock.patch.object(migration, '_find_migrate_repo', return_value='repo')
@mock.patch.object(versioning_api, 'upgrade')
@mock.patch.object(versioning_api, 'downgrade')
@mock.patch.object(migration, 'get_engine', return_value='engine')
class TestDbSync(test.NoDBTestCase):

    def test_version_none(self, mock_get_engine, mock_downgrade, mock_upgrade,
            mock_find_repo, mock_version):
        database = 'fake'
        migration.db_sync(database=database)
        mock_version.assert_called_once_with(database, context=None)
        mock_find_repo.assert_called_once_with(database)
        mock_get_engine.assert_called_once_with(database, context=None)
        mock_upgrade.assert_called_once_with('engine', 'repo', None)
        self.assertFalse(mock_downgrade.called)

    def test_downgrade(self, mock_get_engine, mock_downgrade, mock_upgrade,
            mock_find_repo, mock_version):
        database = 'fake'
        migration.db_sync(1, database=database)
        mock_version.assert_called_once_with(database, context=None)
        mock_find_repo.assert_called_once_with(database)
        mock_get_engine.assert_called_once_with(database, context=None)
        mock_downgrade.assert_called_once_with('engine', 'repo', 1)
        self.assertFalse(mock_upgrade.called)


@mock.patch.object(migration, '_find_migrate_repo', return_value='repo')
@mock.patch.object(versioning_api, 'db_version')
@mock.patch.object(migration, 'get_engine')
class TestDbVersion(test.NoDBTestCase):

    def test_db_version(self, mock_get_engine, mock_db_version,
            mock_find_repo):
        database = 'fake'
        mock_get_engine.return_value = 'engine'
        migration.db_version(database)
        mock_find_repo.assert_called_once_with(database)
        mock_db_version.assert_called_once_with('engine', 'repo')

    def test_not_controlled(self, mock_get_engine, mock_db_version,
            mock_find_repo):
        database = 'api'
        mock_get_engine.side_effect = ['engine', 'engine', 'engine']
        exc = versioning_exceptions.DatabaseNotControlledError()
        mock_db_version.side_effect = [exc, '']
        metadata = mock.MagicMock()
        metadata.tables.return_value = []
        with mock.patch.object(sqlalchemy, 'MetaData',
                metadata), mock.patch.object(migration,
                        'db_version_control') as mock_version_control:
            migration.db_version(database)
            mock_version_control.assert_called_once_with(0,
                                                         database,
                                                         context=None)
            db_version_calls = [mock.call('engine', 'repo')] * 2
            self.assertEqual(db_version_calls, mock_db_version.call_args_list)
        engine_calls = [mock.call(database, context=None)] * 3
        self.assertEqual(engine_calls, mock_get_engine.call_args_list)

    def test_db_version_init_race(self, mock_get_engine, mock_db_version,
            mock_find_repo):
        # This test exercises bug 1804652 by causing
        # versioning_api.version_contro() to raise an unhandleable error the
        # first time it is called.
        database = 'api'
        mock_get_engine.return_value = 'engine'
        exc = versioning_exceptions.DatabaseNotControlledError()
        mock_db_version.side_effect = [exc, '']
        metadata = mock.MagicMock()
        metadata.tables.return_value = []
        with mock.patch.object(sqlalchemy, 'MetaData',
                metadata), mock.patch.object(migration,
                        'db_version_control') as mock_version_control:
            # db_version_control raises an unhandleable error because we were
            # racing to initialise with another process.
            mock_version_control.side_effect = test.TestingException
            migration.db_version(database)
            mock_version_control.assert_called_once_with(0,
                                                         database,
                                                         context=None)
            db_version_calls = [mock.call('engine', 'repo')] * 2
            self.assertEqual(db_version_calls, mock_db_version.call_args_list)
        engine_calls = [mock.call(database, context=None)] * 3
        self.assertEqual(engine_calls, mock_get_engine.call_args_list)

    def test_db_version_raise_on_error(self, mock_get_engine, mock_db_version,
            mock_find_repo):
        # This test asserts that we will still raise a persistent error after
        # working around bug 1804652.
        database = 'api'
        mock_get_engine.return_value = 'engine'
        mock_db_version.side_effect = \
                versioning_exceptions.DatabaseNotControlledError
        metadata = mock.MagicMock()
        metadata.tables.return_value = []
        with mock.patch.object(sqlalchemy, 'MetaData',
                metadata), mock.patch.object(migration,
                        'db_version_control') as mock_version_control:
            # db_version_control raises an unhandleable error because we were
            # racing to initialise with another process.
            mock_version_control.side_effect = test.TestingException
            self.assertRaises(test.TestingException,
                              migration.db_version, database)


@mock.patch.object(migration, '_find_migrate_repo', return_value='repo')
@mock.patch.object(migration, 'get_engine', return_value='engine')
@mock.patch.object(versioning_api, 'version_control')
class TestDbVersionControl(test.NoDBTestCase):

    def test_version_control(self, mock_version_control, mock_get_engine,
            mock_find_repo):
        database = 'fake'
        migration.db_version_control(database=database)
        mock_find_repo.assert_called_once_with(database)
        mock_version_control.assert_called_once_with('engine', 'repo', None)


class TestGetEngine(test.NoDBTestCase):

    def test_get_main_engine(self):
        with mock.patch.object(db_api, 'get_engine',
                return_value='engine') as mock_get_engine:
            engine = migration.get_engine()
            self.assertEqual('engine', engine)
            mock_get_engine.assert_called_once_with(context=None)

    def test_get_api_engine(self):
        with mock.patch.object(db_api, 'get_api_engine',
                return_value='api_engine') as mock_get_engine:
            engine = migration.get_engine('api')
            self.assertEqual('api_engine', engine)
            mock_get_engine.assert_called_once_with()


class TestNewtonCellsCheck(test.NoDBTestCase):
    USES_DB_SELF = True

    def setUp(self):
        super(TestNewtonCellsCheck, self).setUp()
        self.useFixture(nova_fixtures.Database('api', 28))
        self.context = context.get_admin_context()
        self.migration = importlib.import_module(
            'nova.db.sqlalchemy.api_migrations.migrate_repo.versions.'
            '030_require_cell_setup')
        self.engine = db_api.get_api_engine()

    def _flavor_me(self):
        # We can't use the Flavor object or model to create the flavor because
        # the model and object have the description field now but at this point
        # we have not run the migration schema to add the description column.
        flavors = db_utils.get_table(self.engine, 'flavors')
        values = dict(name='foo', memory_mb=123,
                      vcpus=1, root_gb=1,
                      flavorid='m1.foo', swap=0)
        flavors.insert().execute(values)

    def _create_cell_mapping(self, **values):
        mappings = db_utils.get_table(self.engine, 'cell_mappings')
        return mappings.insert().execute(**values).inserted_primary_key[0]

    def _create_host_mapping(self, **values):
        mappings = db_utils.get_table(self.engine, 'host_mappings')
        return mappings.insert().execute(**values).inserted_primary_key[0]

    def test_upgrade_with_no_cell_mappings(self):
        self._flavor_me()
        self.assertRaisesRegex(exception.ValidationError,
                               'Cell mappings',
                               self.migration.upgrade, self.engine)

    def test_upgrade_with_only_cell0(self):
        self._flavor_me()
        self._create_cell_mapping(uuid=objects.CellMapping.CELL0_UUID,
                                  name='cell0',
                                  transport_url='fake',
                                  database_connection='fake')
        self.assertRaisesRegex(exception.ValidationError,
                               'Cell mappings',
                               self.migration.upgrade, self.engine)

    def test_upgrade_without_cell0(self):
        self._flavor_me()
        self._create_cell_mapping(uuid=uuidsentinel.cell1,
                                  name='cell1',
                                  transport_url='fake',
                                  database_connection='fake')
        self._create_cell_mapping(uuid=uuidsentinel.cell2,
                                  name='cell2',
                                  transport_url='fake',
                                  database_connection='fake')
        self.assertRaisesRegex(exception.ValidationError,
                               'Cell0',
                               self.migration.upgrade, self.engine)

    def test_upgrade_with_no_host_mappings(self):
        self._flavor_me()
        self._create_cell_mapping(uuid=objects.CellMapping.CELL0_UUID,
                                  name='cell0',
                                  transport_url='fake',
                                  database_connection='fake')
        self._create_cell_mapping(uuid=uuidsentinel.cell1,
                                  name='cell1',
                                  transport_url='fake',
                                  database_connection='fake')

        with mock.patch.object(self.migration, 'LOG') as log:
            self.migration.upgrade(self.engine)
            self.assertTrue(log.warning.called)

    def test_upgrade_with_required_mappings(self):
        self._flavor_me()
        self._create_cell_mapping(uuid=objects.CellMapping.CELL0_UUID,
                                  name='cell0',
                                  transport_url='fake',
                                  database_connection='fake')
        cell1_id = self._create_cell_mapping(uuid=uuidsentinel.cell1,
                                             name='cell1',
                                             transport_url='fake',
                                             database_connection='fake')
        self._create_host_mapping(cell_id=cell1_id, host='foo')

        self.migration.upgrade(self.engine)

    def test_upgrade_new_deploy(self):
        self.migration.upgrade(self.engine)
