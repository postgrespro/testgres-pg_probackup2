# testgres - pg_probackup2

Control and testing utility for [pg_probackup2](https://github.com/postgrespro/pg_probackup). Python 3.5+ is supported.


## Installation

To install `testgres`, run:

```
pip install testgres-pg_probackup
```

We encourage you to use `virtualenv` for your testing environment.
The package requires testgres~=1.9.3.

## Usage

### Environment variables

| Variable | Required | Default value | Description |
| - | - | - | - |
| PGPROBACKUP_TMP_DIR | No | tests/tmp_dirs | The root of the temporary directory hierarchy where tests store data and logs. Relative paths start from the current working directory. |
| PG_PROBACKUP_TEST_BACKUP_DIR_PREFIX | No | Temporary test hierarchy | Prefix of the test backup directories. Must be an absolute path. Use this variable to store test backups in a location other than the temporary test hierarchy. |
| PG_PROBACKUP_VALGRIND | No | Not set | Run pg_probackup through valgrind if the variable is set to 'y'. Setting  PG_PROBACKUP_VALGRIND_SUP (see below) to any value enables valgrind just like setting `PG_PROBACKUP_VALGRIND=y` would do. |
| PG_PROBACKUP_VALGRIND_SUP | No | Not set | Specify the path to a valgrind suppression file. If the variable is not set then a file named "valgrind.supp" is searched for in the current working directory (normally the root of pg_probackup repository). Setting  PG_PROBACKUP_VALGRIND_SUP to any value enables valgrind just like setting `PG_PROBACKUP_VALGRIND=y` (see above) would do. |

See [Testgres](https://github.com/postgrespro/testgres/tree/master#environment) on how to configure a custom Postgres installation using `PG_CONFIG` and `PG_BIN` environment variables.

### Examples

Here is an example of what you can do with `testgres-pg_probackup2`:

```python
# You can see full script here plugins/pg_probackup2/pg_probackup2/tests/basic_test.py
def test_full_backup(self):
    # Setting up a simple test node
    node = self.pg_node.make_simple('node', pg_options={"fsync": "off", "synchronous_commit": "off"})

    # Initialize and configure Probackup
    self.pb.init()
    self.pb.add_instance('node', node)
    self.pb.set_archiving('node', node)

    # Start the node and initialize pgbench
    node.slow_start()
    node.pgbench_init(scale=100, no_vacuum=True)

    # Perform backup and validation
    backup_id = self.pb.backup_node('node', node)
    out = self.pb.validate('node', backup_id)

    # Check if the backup is valid
    self.assertIn(f"INFO: Backup {backup_id} is valid", out)
```

## Authors

[Postgres Professional](https://postgrespro.ru/about)
