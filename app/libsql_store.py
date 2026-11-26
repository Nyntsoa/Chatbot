import sqlite3

import libsql


class Row:
    def __init__(self, columns, values):
        self.columns = columns
        self.values = values

    def keys(self):
        return self.columns.keys()

    def __getitem__(self, key):
        return self.values[self.columns[key] if isinstance(key, str) else key]


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor
        self.columns = {
            column[0]: index for index, column in enumerate(cursor.description or ())
        }

    @property
    def lastrowid(self):
        return self.cursor.lastrowid

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def fetchone(self):
        values = self.cursor.fetchone()
        return None if values is None else Row(self.columns, values)

    def fetchall(self):
        return [Row(self.columns, values) for values in self.cursor.fetchall()]

    def __iter__(self):
        while (row := self.fetchone()) is not None:
            yield row


class Connection:
    def __init__(self, url, token):
        self.connection = libsql.connect(
            url, auth_token=token, timeout=12, isolation_level=None
        )

    def execute(self, sql, parameters=()):
        try:
            return Cursor(self.connection.execute(sql, tuple(parameters)))
        except libsql.Error as error:
            if "constraint failed" in str(error).casefold():
                raise sqlite3.IntegrityError(str(error)) from error
            raise

    def executemany(self, sql, rows):
        return Cursor(self.connection.executemany(sql, list(rows)))

    def executescript(self, sql):
        self.connection.executescript(sql)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()
