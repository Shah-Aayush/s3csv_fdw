from setuptools import setup
import os 

def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()

setup(
    name='S3Fdw',
    version='0.3.0',
    author='Aayush Shah',
    author_email='shahaayush349@gmail.com',
    packages=['s3fdw'],
    url='https://github.com/Shah-Aayush/hydradatabase-s3csv_fdw',
    license='LICENSE.txt',
    description='PostgreSQL Foreign Data Wrapper for Amazon S3 and S3-compatible storage',
    long_description=read('README.rst'),
    install_requires=[
        "boto3>=1.26.0",
        "botocore>=1.29.0"
    ],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Plugins",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
    ],
    python_requires='>=3.7',
)