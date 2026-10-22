from src import __version__ as pg_probackup2_version

from packaging.version import Version


class TestCommon:
    def test_pg_probackup2_version(self):
        assert type(pg_probackup2_version) is str

        v = Version(pg_probackup2_version)

        assert v.major == 0
        assert v.minor == 10
        assert v.micro == 0

        assert str(v) == pg_probackup2_version
        return
