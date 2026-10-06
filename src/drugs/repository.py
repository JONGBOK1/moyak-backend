"""Use the existing catalog tables without creating or importing medication rows."""
from sqlalchemy import MetaData, Table, inspect, or_, select


class CatalogNotConfigured(Exception):
    pass


CATALOG_COLUMNS = {
    "drugs": ("item_seq", "item_name", "entp_name", "bizrno", "efcy", "use_method"),
    "drug_ingredients": ("item_seq", "ingr_code", "ingr_name", "ingr_eng_name", "item_name", "source"),
    "drug_permissions": ("item_seq", "item_name", "item_eng_name", "entp_name", "etc_otc_name",
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


def list_drugs(db, query=None, limit=20, offset=0):
    table = catalog_table(db, "drugs")
    stmt = select(*projection(table)).order_by(table.c.item_seq).offset(offset).limit(limit + 1)
    if query:
        term = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(or_(table.c.item_name.ilike(f"%{term}%", escape="\\"), table.c.item_seq == query))
    rows = [dict(row) for row in db.execute(stmt).mappings()]
    return {"items": rows[:limit], "limit": limit, "offset": offset, "has_more": len(rows) > limit}


def get_drug(db, item_seq):
    table = catalog_table(db, "drugs")
    row = db.execute(select(*projection(table)).where(table.c.item_seq == item_seq)).mappings().first()
    return dict(row) if row else None


def related(db, item_seq, name, limit=50, offset=0):
    table = catalog_table(db, name)
    ordering = [table.c.item_seq]
    if "ingr_code" in table.c:
        ordering.append(table.c.ingr_code)
    stmt = select(*projection(table)).where(table.c.item_seq == item_seq).order_by(*ordering).offset(offset).limit(limit + 1)
    rows = [dict(row) for row in db.execute(stmt).mappings()]
    return {"items": rows[:limit], "limit": limit, "offset": offset, "has_more": len(rows) > limit}
