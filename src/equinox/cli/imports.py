"""Import commands — Postman and OpenAPI/Swagger."""

import logging
import sys

import click

from equinox.storage import CollectionManager

logger = logging.getLogger(__name__)


@click.group("import")
def import_cmd():
    """Import collections from various formats"""
    pass


@import_cmd.command("postman")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--preview", is_flag=True, help="Preview without importing")
def import_postman(file_path, preview):
    """Import Postman collection"""
    from pathlib import Path
    from equinox.importers import PostmanImporter, preview_collection

    file = Path(file_path)

    if preview:
        try:
            info = preview_collection(file)
            click.echo(f"Collection: {info['name']}")
            click.echo(f"Version: {info['version']}")
            click.echo(f"Requests: {info['request_count']}")
            click.echo(f"Size: {info['size_bytes']:,} bytes")
            if info['description']:
                click.echo(f"Description: {info['description']}")
        except Exception as exc:
            logger.error("Failed to preview Postman file %s: %s", file_path, exc)
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)
    else:
        from equinox.cli.main import get_db
        db = get_db()
        manager = CollectionManager(db)
        importer = PostmanImporter(manager)
        try:
            logger.info("Importing Postman collection from %s", file_path)
            collection_id = importer.import_file(file)
            logger.info("Postman import complete: collection_id=%s", collection_id)
            click.echo(f"✓ Successfully imported to collection ID: {collection_id}")
        except Exception as exc:
            logger.error("Postman import failed for %s: %s", file_path, exc, exc_info=True)
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)


@import_cmd.command("openapi")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--preview", is_flag=True, help="Preview without importing")
def import_openapi(file_path, preview):
    """Import OpenAPI/Swagger specification"""
    from pathlib import Path
    from equinox.importers import OpenAPIImporter, preview_spec

    file = Path(file_path)

    if preview:
        try:
            info = preview_spec(file)
            click.echo(f"API: {info['title']}")
            click.echo(f"Version: {info['version']}")
            click.echo(f"OpenAPI: {info['openapi_version']}")
            click.echo(f"Paths: {info['path_count']}")
            click.echo(f"Operations: {info['operation_count']}")
            click.echo(f"Size: {info['size_bytes']:,} bytes")
            if info['description']:
                click.echo(f"Description: {info['description']}")
        except Exception as exc:
            logger.error("Failed to preview OpenAPI spec %s: %s", file_path, exc)
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)
    else:
        from equinox.cli.main import get_db
        db = get_db()
        manager = CollectionManager(db)
        importer = OpenAPIImporter(manager)
        try:
            logger.info("Importing OpenAPI spec from %s", file_path)
            collection_id = importer.import_file(file)
            logger.info("OpenAPI import complete: collection_id=%s", collection_id)
            click.echo(f"✓ Successfully imported to collection ID: {collection_id}")
        except Exception as exc:
            logger.error("OpenAPI import failed for %s: %s", file_path, exc, exc_info=True)
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)


@import_cmd.command("har")
@click.argument("file_path", type=click.Path(exists=True))
def import_har(file_path):
    """Import requests from a HAR (HTTP Archive) file"""
    from pathlib import Path
    from equinox.importers import HARImporter
    from equinox.cli.main import get_db

    file = Path(file_path)
    db = get_db()
    manager = CollectionManager(db)
    importer = HARImporter(manager)
    try:
        logger.info("Importing HAR file from %s", file_path)
        collection_id = importer.import_file(file)
        logger.info("HAR import complete: collection_id=%s", collection_id)
        click.echo(f"✓ Successfully imported HAR to collection ID: {collection_id}")
    except Exception as exc:
        logger.error("HAR import failed for %s: %s", file_path, exc, exc_info=True)
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
