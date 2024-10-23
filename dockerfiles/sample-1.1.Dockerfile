ARG PGVERSION=15

# Use the Hydra base image
# FROM postgres:15
FROM ghcr.io/hydradatabase/hydra:15

USER root

# Install necessary packages
RUN apt-get update && \
    apt-get install -y wget curl \
    build-essential postgresql-server-dev-15 libcurl4-openssl-dev libssl-dev git \
    postgresql-plpython3-15 python3-pip \
    python3-boto3 postgresql-contrib-15 jq vim nano sysstat ioping dstat procps \
    postgresql-15-cron python3-dev

# Clone the repository
RUN git clone --branch enhanced https://github.com/Shah-Aayush/hydradatabase-s3csv_fdw.git /tmp/s3fdw

# Set the working directory
WORKDIR /tmp/s3fdw

# Install S3 FDW
RUN pip3 install --break-system-packages -e .

# Set PYTHONPATH to include the path where s3fdw is installed
ENV PYTHONPATH="/usr/local/lib/python3.11/dist-packages:/tmp/s3fdw:$PYTHONPATH"

# Download and install pg_profile extension
RUN wget https://github.com/zubkov-andrei/pg_profile/releases/download/4.6/pg_profile--4.6.tar.gz -O /tmp/pg_profile.tar.gz && \
    mkdir -p /usr/share/postgresql/15/extension && \
    tar xzf /tmp/pg_profile.tar.gz --directory /usr/share/postgresql/15/extension && \
    rm /tmp/pg_profile.tar.gz

# Clone the aws-s3 extension repository
RUN git clone https://github.com/chimpler/postgres-aws-s3 /tmp/postgres-aws-s3

# Build and install the extension
RUN cd /tmp/postgres-aws-s3 \
    && make \
    && make install

# Install pg_cron extension
RUN apt-get install -y postgresql-15-cron

# Create SQL script to install extensions
RUN echo "CREATE EXTENSION IF NOT EXISTS plpython3u;" >> /docker-entrypoint-initdb.d/init_extensions.sql && \
    echo "CREATE EXTENSION IF NOT EXISTS aws_s3 CASCADE;" >> /docker-entrypoint-initdb.d/init_extensions.sql && \
    echo "CREATE EXTENSION IF NOT EXISTS dblink;" >> /docker-entrypoint-initdb.d/init_extensions.sql && \
    echo "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;" >> /docker-entrypoint-initdb.d/init_extensions.sql && \
    echo "CREATE EXTENSION IF NOT EXISTS pg_cron;" >> /docker-entrypoint-initdb.d/init_extensions.sql

# Modify postgresql.conf
RUN echo "shared_preload_libraries = 'pg_stat_statements,pg_cron'" >> /usr/share/postgresql/15/postgresql.conf.sample && \
    echo "cron.database_name = 'postgres'" >> /usr/share/postgresql/15/postgresql.conf.sample


EXPOSE 5432