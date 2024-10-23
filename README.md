# S3 CSV Foreign Data Wrapper for PostgreSQL

This data wrapper adds the ability to perform `SELECT *` queries on CSV files stored on the Amazon S3 file system. This is meant to replace [s3_fdw](https://github.com/umitanuki/s3_fdw) which is not supported on PostgreSQL version 9.2+.

## Installation

First you need to install it (last command might need a sudo).

```bash
git clone git@github.com:eligoenergy/s3csv_fdw.git
cd s3csv_fdw
python setup.py install
```

Then activate the Multicorn extension in your PostgreSQL database:

```sql
CREATE EXTENSION multicorn;
```

## Create Foreign Data Wrapper

Create the server by executing the following SQL:

```sql
CREATE SERVER multicorn_csv FOREIGN DATA WRAPPER multicorn
OPTIONS (
    wrapper 's3csvfdw.s3csvfdw.S3CsvFdw'
);
```

## Create Foreign Table

Replace the example fields with your information:

Example:

```sql
CREATE FOREIGN TABLE test (
    remote_field1  character varying,
    remote_field2  integer
) SERVER multicorn_csv OPTIONS (
    aws_access_key '

your

_key',
    aws_secret_key 'your_secret',
    bucket 'your_bucket',
    filename 'your_file.csv'
);
```

## Add User Credentials

Store your AWS credentials into a PostgreSQL user mapping:

Example:

```sql
CREATE USER MAPPING FOR my_pg_user SERVER multicorn_csv OPTIONS (
    aws_access_key 'XXXXXXXXXXXXXXX',
    aws_secret_key 'XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX'
);
```

## Perform Queries

You now have a PostgreSQL table. For now, only read queries are supported:

```sql
SELECT * FROM test;
```

## Custom Options

You can customize the foreign table with additional options:

```sql
CREATE FOREIGN TABLE s3_csv_data (
    id INT,
    name TEXT,
    age INT
) SERVER multicorn_csv OPTIONS (
    aws_access_key 'your_key',
    aws_secret_key 'your_secret',
    bucket 'your_bucket',
    filename 'your_file.csv',
    endpoint 'https://your-custom-endpoint',
    verify_ssl 'false',
    signature_version 's3v4',
    addressing_style 'path',
    region 'your_region'
);
```

## Credits

Christian Toivola ([dev360](https://github.com/dev360)) wrote the code and submitted it as a Multicorn request [here](https://github.com/Kozea/Multicorn/pull/49). This package is a continuation of that work.