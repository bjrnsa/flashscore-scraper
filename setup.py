from setuptools import find_packages, setup

setup(
    name="flashscore-scraper",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "beautifulsoup4>=4.9.3",
        "selenium>=4.0.0",
        "pyyaml>=5.4.1",
        "tqdm>=4.65.0",
    ],
    author="Bjørn Aagaard",
    author_email="aagaard@gmail.com",
    description="A Python package for scraping handball match data from Flashscore",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/bjrnsa/flashscore-scraper",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
    ],
    python_requires=">=3.8",
    include_package_data=True,
    package_data={
        "flashscore_scraper": ["config/*.yaml"],
    },
)
