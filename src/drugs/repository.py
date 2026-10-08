"""Use the existing catalog tables without creating or importing medication rows."""
import json
from pathlib import Path
from sqlalchemy import MetaData, Table, and_, func, inspect, literal, not_, or_, select, union_all

# Compatibility scope only: names and all medication data are read from the DB.
# These nine items were in the former search CSV but are absent from drugs.
LEGACY_PERMISSION_IDS = tuple(json.loads(
    Path(__file__).with_name('legacy_permission_ids.json').read_text(encoding='utf-8')))


class CatalogNotConfigured(Exception):
    pass


CATALOG_COLUMNS = {
    "drugs": ("item_seq", "item_name", "entp_name", "bizrno", "efcy", "use_method"),
    "drug_ingredients": ("item_seq", "ingr_code", "ingr_name", "ingr_eng_name", "item_name", "source"),
    "drug_permissions": ("item_seq", "item_name", "item_eng_name", "entp_name", "bizrno", "etc_otc_name",
                         "class_name", "material_name", "main_item_ingr", "ingr_name", "atc_code",
                         "pack_unit", "storage_method", "valid_term", "total_content", "chart",
                         "item_permit_date", "cancel_name", "cancel_date", "edi_code", "bar_code",
                         "doc_ee_url", "doc_ud_url", "doc_nb_url"),
    "drug_pills": ("item_seq", "item_name", "entp_name", "drug_shape", "color_class1", "color_class2",
                   "print_front", "print_back", "line_front", "line_back", "leng_long", "leng_short",
                   "thick", "chart", "item_image", "form_code_name", "class_name", "etc_otc_name"),
}


def catalog_table(db, name):
    if name not in CATALOG_COLUMNS:
        raise ValueError("Unknown catalog table")
    connection = db.connection()
    schema = "public" if connection.dialect.name == "postgresql" else None
    if not inspect(connection).has_table(name, schema=schema):
        raise CatalogNotConfigured("의약품 DB 연결 및 테이블 구성을 먼저 완료해주세요.")
    table = Table(name, MetaData(), schema=schema, autoload_with=connection)
    required = {"item_seq", "item_name"} if name == "drugs" else {"item_seq"}
    if not required.issubset(table.c.keys()):
        raise CatalogNotConfigured("의약품 테이블 구조 확인이 필요합니다.")
    return table


def projection(table):
    return [table.c[name] for name in CATALOG_COLUMNS[table.name] if name in table.c]


def _column_or_null(table, name):
    return table.c[name] if name in table.c else literal(None)


def _search_filter(table, query):
    if not query:
        return None
    term = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return or_(table.c.item_name.ilike(f"%{term}%", escape="\\"), table.c.item_seq == query)


def list_drugs(db, query=None, limit=20, offset=0):
    drugs = catalog_table(db, "drugs")
    permissions = catalog_table(db, "drug_permissions")
    columns = ("item_seq", "item_name", "entp_name", "bizrno", "efcy", "use_method")

    drug_filter = _search_filter(drugs, query)
    drug_stmt = select(
        *(drugs.c[name].label(name) if name in drugs.c else literal(None).label(name)
          for name in columns)
    )
    if drug_filter is not None:
        drug_stmt = drug_stmt.where(drug_filter)

    permission_name = func.coalesce(
        permissions.c.item_name,
        _column_or_null(permissions, "item_eng_name"),
        literal(""),
    )
    permission_filter = and_(permissions.c.item_seq.in_(LEGACY_PERMISSION_IDS), not_(select(drugs.c.item_seq).where(
        drugs.c.item_seq == permissions.c.item_seq
    ).exists()))
    if query:
        term = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        permission_filter = and_(
            permission_filter,
            or_(permission_name.ilike(f"%{term}%", escape="\\"), permissions.c.item_seq == query),
        )
    permission_rows = select(
        permissions.c.item_seq.label("item_seq"),
        permission_name.label("item_name"),
        _column_or_null(permissions, "entp_name").label("entp_name"),
        _column_or_null(permissions, "bizrno").label("bizrno"),
        literal(None).label("efcy"),
        literal(None).label("use_method"),
        func.row_number().over(
            partition_by=permissions.c.item_seq,
            order_by=(permission_name, _column_or_null(permissions, "entp_name")),
        ).label("row_number"),
    ).where(permission_filter).subquery()
    permission_stmt = select(*(permission_rows.c[name] for name in columns)).where(
        permission_rows.c.row_number == 1
    )

    catalog = union_all(drug_stmt, permission_stmt).subquery()
    stmt = select(catalog).order_by(catalog.c.item_seq).offset(offset).limit(limit + 1)
    rows = [dict(row) for row in db.execute(stmt).mappings()]
    return {"items": rows[:limit], "limit": limit, "offset": offset, "has_more": len(rows) > limit}


def get_drug(db, item_seq):
    table = catalog_table(db, "drugs")
    row = db.execute(select(*projection(table)).where(table.c.item_seq == item_seq)).mappings().first()
    if row:
        return dict(row)

    if item_seq not in LEGACY_PERMISSION_IDS:
        return None

    permissions = catalog_table(db, "drug_permissions")
    permission_row = db.execute(
        select(
            permissions.c.item_seq,
            func.coalesce(permissions.c.item_name, _column_or_null(permissions, "item_eng_name")).label("item_name"),
            _column_or_null(permissions, "entp_name").label("entp_name"),
            _column_or_null(permissions, "bizrno").label("bizrno"),
        ).where(permissions.c.item_seq == item_seq).limit(1)
    ).mappings().first()
    if permission_row is None:
        return None
    return {**dict(permission_row), "efcy": None, "use_method": None}


def related(db, item_seq, name, limit=50, offset=0):
    table = catalog_table(db, name)
    ordering = [table.c.item_seq]
    if "ingr_code" in table.c:
        ordering.append(table.c.ingr_code)
    stmt = select(*projection(table)).where(table.c.item_seq == item_seq).order_by(*ordering).offset(offset).limit(limit + 1)
    rows = [dict(row) for row in db.execute(stmt).mappings()]
    return {"items": rows[:limit], "limit": limit, "offset": offset, "has_more": len(rows) > limit}
