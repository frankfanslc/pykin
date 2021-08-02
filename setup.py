from setuptools import setup, find_packages

# read the contents of your README file
from os import path
this_directory = path.abspath(path.dirname(__file__))
with open(path.join(this_directory, 'README.md'), encoding='utf-8') as f:
    lines = f.readlines()

# remove images from README
lines = [x for x in lines if '.png' not in x]
long_description = ''.join(lines)

setup(
    name="pykin",
    packages=find_packages(exclude = []),
    install_requires=[
        "numpy>=1.13.3",
        "matplotlib>=3.3.4",
    ],
    eager_resources=['*'],
    include_package_data=True,
    python_requires='>=3',
    description="Robotics Kinematics Library",
    author="Dae Jong Jin",
    url="https://github.com/jdj2261/pykin.git",
    download_url="https://github.com/jdj2261/pykin/archive/refs/heads/main.zip",
    author_email="wlseoeo@gmain.com",
    version="0.0.1",
    long_description=long_description,
    long_description_content_type='text/markdown'
)