from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="equinox",
    version="0.3.3",
    author="Equinox Team",
    description="A local-first API testing tool with CLI and GUI",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/i3iorn/equinox",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Testing",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    python_requires=">=3.9",
    install_requires=[
        "httpx>=0.24.0",
        "click>=8.0.0",
        "PyQt6>=6.5.0",
        "pygments>=2.15.0",
        "jsonschema>=4.0.0",
        "pyyaml>=6.0.0",
        "python-dotenv>=1.0.0",
        "cryptography>=41.0.0",
        "certifi>=2023.0.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-asyncio>=0.21.0",
            "pytest-cov>=4.0.0",
            "pytest-mock>=3.12.0",
            "black>=23.0.0",
            "flake8>=6.0.0",
            "mypy>=1.0.0",
            "bandit>=1.7.0",
            "safety>=2.3.0",
            "pre-commit>=3.0.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "equinox=equinox.cli.main:main_entry"
        ]
    },
)
