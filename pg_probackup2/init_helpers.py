import logging
from functools import reduce
import getpass
import os
import pefile
import re
import shutil
import subprocess
import sys
import traceback
import testgres
import typing

try:
    import lz4.frame  # noqa: F401

    HAVE_LZ4 = True
except ImportError as e:
    HAVE_LZ4 = False
    LZ4_error = e

try:
    import zstd  # noqa: F401

    HAVE_ZSTD = True
except ImportError as e:
    HAVE_ZSTD = False
    ZSTD_error = e

delete_logs = os.getenv('KEEP_LOGS') not in ['1', 'y', 'Y']

try:
    testgres.configure_testgres(
        cache_initdb=False,
        cached_initdb_dir=False,
        node_cleanup_full=delete_logs)
except Exception as e:
    logging.warning("Can't configure testgres: {0}".format(e))


class InitData_ServerProps:
    pgpro_edition: typing.Optional[str]
    num_version: int

    def __init__(
        self,
        pgpro_edition: typing.Optional[str],
        num_version: int,
    ):
        assert pgpro_edition is None or type(pgpro_edition) is str
        assert type(num_version) is int
        self.pgpro_edition = pgpro_edition
        self.num_version = num_version
        return


class InitData_BinaryName:
    pg_config: str
    postgres: str
    initdb: str

    def __init__(
        self,
        pg_config: str,
        initdb: str,
        postgres: str,
    ):
        assert type(pg_config) is str
        assert type(postgres) is str
        assert type(initdb) is str

        self.pg_config = pg_config
        self.postgres = postgres
        self.initdb = initdb
        return


class Init(object):
    _server_props: InitData_ServerProps

    def __init__(self):
        if '-v' in sys.argv or '--verbose' in sys.argv:
            self.verbose = True
        else:
            self.verbose = False

        os_ops = testgres.LocalOperations()
        os_name = os_ops.get_name()

        pg_bin = testgres.get_bin_dir(os_ops)
        if pg_bin is None:
            raise testgres.InvalidOperationException(
                "Failed to determine the Postgres binary directory. Specify the path to the directory in PG_BIN or put it into the system PATH.",
            )

        if not os_ops.path_exists(pg_bin):
            raise testgres.InvalidOperationException(
                "Path [{}] is not found.".format(pg_bin),
            )

        pg_config_path = os_ops.build_path(
            pg_bin,
            __class__._get_binary_names(os_ops).pg_config,
        )

        assert type(pg_config_path) is str

        if os_ops.path_exists(pg_config_path):
            self._server_props = __class__._init__get_server_props__via_pg_config(
                os_ops,
                pg_config_path,
            )
        else:
            self._server_props = __class__._init__get_server_props__via_server(
                os_ops,
                pg_bin,
            )

        # TODO: Always test with NLS support and remove this flag
        self.is_nls_enabled = True

        postgres = os_ops.build_path(pg_bin, 'postgres.exe' if os_name == 'nt' else 'postgres')

        if os_name == 'posix':
            ldd = os_ops.exec_command(
                ['ldd', postgres],
                encoding='utf-8')
            self.is_lz4_enabled = 'liblz4.so' in ldd
        elif os_name == 'nt':
            pe = pefile.PE(postgres, fast_load=True)
            pe.parse_data_directories(directories=[
                pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
            self.is_lz4_enabled = False
            for entry in pe.DIRECTORY_ENTRY_IMPORT:
                if entry.dll.decode('utf-8') == 'liblz4.dll':
                    self.is_lz4_enabled = True
                    break
        else:
            # Fall back to trying to determine lz4 support via pg_config
            pg_config_data = testgres.utils.get_pg_config2(
                pg_config_path=pg_config_path,
                os_ops=os_ops,
            )
            self.is_lz4_enabled = '-llz4' in pg_config_data['LIBS']

        os.environ['LANGUAGE'] = 'en'   # set default locale language to en. All messages will use this locale
        test_env = os.environ.copy()
        envs_list = [
            'LANGUAGE',
            'LC_ALL',
            'PGCONNECT_TIMEOUT',
            'PGDATA',
            'PGDATABASE',
            'PGHOSTADDR',
            'PGREQUIRESSL',
            'PGSERVICE',
            'PGSSLMODE',
            'PGUSER',
            'PGPORT',
            'PGHOST'
        ]

        for e in envs_list:
            test_env.pop(e, None)

        test_env['LC_MESSAGES'] = 'C'
        test_env['LC_TIME'] = 'C'
        self._test_env = test_env

        # Get the directory from which the script was executed
        self.source_path = os.getcwd()
        tmp_path = test_env.get('PGPROBACKUP_TMP_DIR')
        if tmp_path and os.path.isabs(tmp_path):
            self.tmp_path = tmp_path
        else:
            self.tmp_path = os.path.abspath(
                os.path.join(self.source_path, tmp_path or os.path.join('tests', 'tmp_dirs'))
            )

        os.makedirs(self.tmp_path, exist_ok=True)

        self.username = getpass.getuser()

        self.probackup_path = None
        if 'PGPROBACKUPBIN' in test_env:
            if shutil.which(test_env["PGPROBACKUPBIN"]):
                self.probackup_path = test_env["PGPROBACKUPBIN"]
            else:
                raise Exception(
                    'pg_probackup binary not found at PGPROBACKUPBIN={0}'.format(
                        test_env["PGPROBACKUPBIN"]))

        if not self.probackup_path:
            probackup_path_tmp = os_ops.build_path(pg_bin, 'pg_probackup')

            if os.path.isfile(probackup_path_tmp):
                if not os.access(probackup_path_tmp, os.X_OK):
                    logging.warning('{0} is not an executable file'.format(
                        probackup_path_tmp))
                else:
                    self.probackup_path = probackup_path_tmp

        if not self.probackup_path:
            probackup_path_tmp = self.source_path

            if os.path.isfile(probackup_path_tmp):
                if not os.access(probackup_path_tmp, os.X_OK):
                    logging.warning('{0} is not an executable file'.format(
                        probackup_path_tmp))
                else:
                    self.probackup_path = probackup_path_tmp

        if not self.probackup_path:
            raise Exception('pg_probackup binary is not found')

        if os_name == 'posix':
            self.EXTERNAL_DIRECTORY_DELIMITER = ':'
            os.environ['PATH'] = os.path.dirname(
                self.probackup_path) + ':' + os.environ['PATH']

        elif os_name == 'nt':
            self.EXTERNAL_DIRECTORY_DELIMITER = ';'
            os.environ['PATH'] = os.path.dirname(
                self.probackup_path) + ';' + os.environ['PATH']

        self.probackup_old_path = None
        if 'PGPROBACKUPBIN_OLD' in test_env:
            if (os.path.isfile(test_env['PGPROBACKUPBIN_OLD']) and os.access(test_env['PGPROBACKUPBIN_OLD'], os.X_OK)):
                self.probackup_old_path = test_env['PGPROBACKUPBIN_OLD']
            else:
                if self.verbose:
                    print('PGPROBACKUPBIN_OLD is not an executable file')

        self.probackup_version = None
        self.old_probackup_version = None

        probackup_version_output = subprocess.check_output(
            [self.probackup_path, "--version"],
            stderr=subprocess.STDOUT,
        ).decode('utf-8')
        match = re.search(r"\d+\.\d+\.\d+",
                          probackup_version_output)
        self.probackup_version = match.group(0) if match else None
        match = re.search(r"\(compressions: ([^)]*)\)", probackup_version_output)
        compressions = match.group(1) if match else None
        if compressions:
            self.probackup_compressions = {s.strip() for s in compressions.split(',')}
        else:
            self.probackup_compressions = []

        if self.probackup_old_path:
            old_probackup_version_output = subprocess.check_output(
                [self.probackup_old_path, "--version"],
                stderr=subprocess.STDOUT,
            ).decode('utf-8')
            match = re.search(r"\d+\.\d+\.\d+",
                              old_probackup_version_output)
            self.old_probackup_version = match.group(0) if match else None

        self.remote = test_env.get('PGPROBACKUP_SSH_REMOTE', None) == 'ON'
        self.ptrack = test_env.get('PG_PROBACKUP_PTRACK', None) == 'ON' and self._server_props.num_version >= 110000
        self.wal_tree_enabled = test_env.get('PG_PROBACKUP_WAL_TREE_ENABLED', None) == 'ON'

        self.bckp_source = test_env.get('PG_PROBACKUP_SOURCE', 'pro').lower()
        if self.bckp_source not in ('base', 'direct', 'pro'):
            raise Exception("Wrong PG_PROBACKUP_SOURCE value. Available options: base|direct|pro")

        self.paranoia = test_env.get('PG_PROBACKUP_PARANOIA', None) == 'ON'
        env_compress = test_env.get('ARCHIVE_COMPRESSION', None)
        if env_compress:
            env_compress = env_compress.lower()
        if env_compress in ('on', 'zlib'):
            self.compress_suffix = '.gz'
            self.archive_compress = 'zlib'
        elif env_compress == 'lz4':
            if not HAVE_LZ4:
                raise LZ4_error
            if 'lz4' not in self.probackup_compressions:
                raise Exception("pg_probackup is not compiled with lz4 support")
            self.compress_suffix = '.lz4'
            self.archive_compress = 'lz4'
        elif env_compress == 'zstd':
            if not HAVE_ZSTD:
                raise ZSTD_error
            if 'zstd' not in self.probackup_compressions:
                raise Exception("pg_probackup is not compiled with zstd support")
            self.compress_suffix = '.zst'
            self.archive_compress = 'zstd'
        else:
            self.compress_suffix = ''
            self.archive_compress = False

        cfs_compress = test_env.get('PG_PROBACKUP_CFS_COMPRESS', None)
        if cfs_compress:
            self.cfs_compress = cfs_compress.lower()
        else:
            self.cfs_compress = self.archive_compress

        os.environ["PGAPPNAME"] = "pg_probackup"
        self.delete_logs = delete_logs

        if self.probackup_version.split('.')[0].isdigit():
            self.major_version = int(self.probackup_version.split('.')[0])
        else:
            raise Exception('Can\'t process pg_probackup version \"{}\": the major version is expected to be a number'.format(self.probackup_version))

        self.valgrind = test_env.get('PG_PROBACKUP_VALGRIND')
        self.valgrind_sup_path = test_env.get('PG_PROBACKUP_VALGRIND_SUP')

    def test_env(self):
        return self._test_env.copy()

    @property
    def is_enterprise(self) -> bool:
        assert type(self._server_props) is InitData_ServerProps
        return self._server_props.pgpro_edition == 'enterprise'

    @property
    def is_shardman(self) -> bool:
        assert type(self._server_props) is InitData_ServerProps
        return self._server_props.pgpro_edition == 'shardman'

    @property
    def is_pgpro(self) -> bool:
        assert type(self._server_props) is InitData_ServerProps
        return self._server_props.pgpro_edition is not None

    @property
    def pg_config_version(self) -> int:
        assert type(self._server_props) is InitData_ServerProps
        return self._server_props.num_version

    @staticmethod
    def _init__get_server_props__via_pg_config(
        os_ops: testgres.OsOperations,
        pg_config_path: str,
    ) -> InitData_ServerProps:
        assert isinstance(os_ops, testgres.OsOperations)
        assert type(pg_config_path) is str

        pg_config = testgres.utils.get_pg_config2(
            pg_config_path=pg_config_path,
            os_ops=os_ops,
        )
        assert type(pg_config) is dict

        pgpro_edition = pg_config.get('PGPRO_EDITION')
        assert pgpro_edition is None or type(pgpro_edition) is str

        version_str = pg_config.get('VERSION', '')
        assert type(version_str) is str

        if not version_str:
            raise RuntimeError("Field 'VERSION' not found in pg_config output")

        version_num = testgres.parse_pg_version(version_str)
        parts = [*version_num.split('.'), '0', '0'][:3]
        parts[0] = re.match(r'\d+', parts[0]).group()

        num_version = reduce(lambda v, x: v * 100 + int(x), parts, 0)
        assert type(num_version) is int

        return InitData_ServerProps(
            pgpro_edition=pgpro_edition,
            num_version=num_version,
        )

    @staticmethod
    def _init__get_server_props__via_server(
        os_ops: testgres.OsOperations,
        pg_bin_dir: str,
    ) -> InitData_ServerProps:
        assert isinstance(os_ops, testgres.OsOperations)
        assert type(pg_bin_dir) is str

        tmpdir = os_ops.mkdtemp()
        assert type(tmpdir) is str
        assert os_ops.path_exists(tmpdir)
        assert os_ops.is_abs_path(tmpdir)

        try:
            initdb = os_ops.build_path(
                pg_bin_dir,
                __class__._get_binary_names(os_ops).initdb,
            )

            os_ops.exec_command(
                [initdb, "-D", tmpdir],
                encoding='utf-8',
            )

            postgres = os_ops.build_path(
                pg_bin_dir,
                __class__._get_binary_names(os_ops).postgres,
            )

            pgpro_edition_out: typing.Optional[str] = None
            try:
                exec_r = os_ops.exec_command(
                    [postgres, "-C", "pgpro_edition", "-D", tmpdir],
                    encoding='utf-8',
                )
                assert type(exec_r) is str
                pgpro_edition_out = exec_r.strip()
            except testgres.ExecUtilException as e:
                assert e.exit_code != 0

                logging.debug("Exception ({}): {}".format(
                    type(e).__name__,
                    e,
                ))

            assert pgpro_edition_out is None or type(pgpro_edition_out) is str
            pgpro_edition = pgpro_edition_out

            server_version_out = os_ops.exec_command(
                [postgres, "-C", "server_version", "-D", tmpdir],
                encoding='utf-8'
            )
            assert type(server_version_out) is str
            server_version = testgres.parse_pg_version(server_version_out)
            parts = [*server_version.split('.'), '0', '0'][:3]
            parts[0] = re.match(r'\d+', parts[0]).group()
            # Server_version consists of two fields (x.y) so num_version always ends with 00
            pg_config_version = reduce(lambda v, x: v * 100 + int(x), parts, 0)
        finally:
            os_ops.rmdirs(tmpdir)

        return InitData_ServerProps(
            pgpro_edition=pgpro_edition,
            num_version=pg_config_version,
        )

    sm_binary_names__win32 = InitData_BinaryName(
        pg_config="pg_config.exe",
        initdb="initdb.exe",
        postgres="postgres.exe",
    )

    sm_binary_names__linux = InitData_BinaryName(
        pg_config="pg_config",
        initdb="initdb",
        postgres="postgres",
    )

    @staticmethod
    def _get_binary_names(
        os_ops: testgres.OsOperations,
    ) -> InitData_BinaryName:
        assert isinstance(os_ops, testgres.OsOperations)

        platform_name = os_ops.get_platform()
        if platform_name == "win32":
            return __class__.sm_binary_names__win32

        return __class__.sm_binary_names__linux


try:
    init_params = Init()
except Exception as e:
    traceback.print_exc(file=sys.stderr)
    logging.error(str(e))
    logging.warning("testgres.plugins.probackup2.init_params is set to None.")
    init_params = None
