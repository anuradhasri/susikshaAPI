from fastapi import HTTPException
from sqlalchemy import func

from app.models.models import Package, Region


def lock_package_centre(db, region_id):
    return db.query(Region.id).filter(Region.id == region_id, Region.deleted_at.is_(None)).with_for_update().first()


def active_packages(db, region_id):
    return db.query(Package).filter(Package.region_id == region_id, Package.is_active.is_(True),
                                    Package.deleted_at.is_(None))


def validate_package_name(db, region_id, name, package_id=None):
    if not name:
        raise HTTPException(422, 'Enter a package name.')
    duplicate = active_packages(db, region_id).filter(func.lower(func.trim(Package.name)) == name.lower())
    if package_id is not None:
        duplicate = duplicate.filter(Package.id != package_id)
    if duplicate.with_for_update().first():
        raise HTTPException(409, 'A package with that name already exists at this centre.')
