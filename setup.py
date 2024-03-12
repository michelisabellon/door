from setuptools import setup, find_packages
from subprocess import check_output

# get the version of gdal-config to use as a requirement (from bash)
gdalconfig_version = check_output('gdal-config --version', shell=True).decode('utf-8').strip()

setup(
    name='door',
    version='0.1',
    packages=find_packages(),
    description='A package for operational retrieval of raster data from different sources',
    author='Luca Trotter',
    author_email='luca.trotter@cimafoundation.org',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Build Tools',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12'
    ],
    keywords='data retrieval, meteorological data, satellite data, climatological data, environmental data, raster data,\
        xarray, netcdf, hdf5, grib, hdf4, hdf-eos, hdf-eos5, geotiff',
    install_requires=[
        f'gdal[numpy]=={gdalconfig_version}',
        'h5py >= 3.4.0',
        'cfgrib >= 0.9.9',
        'xarray>=2023.9.0',
        'rioxarray >= 0.7.0',
        'requests',
        'dask',
        'scipy',
        'ecmwf-opendata >= 0.2.0',
        'cdsapi'
    ],
    python_requires='>=3.10',
    test_suite='tests',
)