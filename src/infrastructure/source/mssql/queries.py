"""MSSQL keşif/extraction SQL'leri (design/02, /03, /04, /05). Salt-okunur, sistem nesneleri elenir."""

DISCOVER_DATABASES = """
SELECT name
FROM sys.databases
WHERE database_id > 4 AND state = 0 AND HAS_DBACCESS(name) = 1
ORDER BY name;
"""

# SP/View/Function/Trigger + tablo (U) tek geçişte. is_ms_shipped=0 → Microsoft nesneleri elenir.
INVENTORY_OBJECTS = """
SELECT s.name AS schema_name, o.name AS object_name, o.type AS type_code,
       o.object_id, o.modify_date
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE o.type IN ('P','V','FN','IF','TF','TR','U')
  AND o.is_ms_shipped = 0
ORDER BY s.name, o.name;
"""

LIST_SYNONYMS = """
SELECT s.name AS schema_name, sy.name AS synonym_name, sy.base_object_name
FROM sys.synonyms sy
JOIN sys.schemas s ON s.schema_id = sy.schema_id;
"""

# Tüm modüller tek sorguda (N+1 yok, design/03). Encrypted → definition NULL.
FETCH_DEFINITIONS = """
SELECT o.object_id, s.name AS schema_name, o.name AS object_name, o.type AS type_code,
       o.modify_date, m.definition,
       m.uses_ansi_nulls, m.uses_quoted_identifier, m.is_recompiled, m.execute_as_principal_id
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
LEFT JOIN sys.sql_modules m ON m.object_id = o.object_id
WHERE o.type IN ('P','V','FN','IF','TF','TR') AND o.is_ms_shipped = 0;
"""

# Nesne-seviyesi extended property (class=1, minor_id=0 → nesne; >0 → kolon, design/03).
FETCH_EXTENDED_PROPERTIES = """
SELECT ep.major_id, ep.minor_id, ep.name AS prop_name,
       CAST(ep.value AS NVARCHAR(MAX)) AS prop_value
FROM sys.extended_properties ep
WHERE ep.class = 1 AND ep.name = 'MS_Description';
"""

# Birincil bağımlılık kaynağı (design/04). is_updated → write, değilse read.
FETCH_DEPENDENCIES = """
SELECT referencing_id,
       referenced_database_name, referenced_schema_name, referenced_entity_name,
       referenced_minor_name, is_updated
FROM sys.sql_expression_dependencies;
"""

# --- Tablo sözlüğü (design/05) ---------------------------------------------
FETCH_COLUMNS = """
SELECT c.object_id, c.column_id, c.name AS column_name,
       t.name AS data_type, t.is_user_defined AS is_udt, bt.name AS base_type,
       c.max_length, c.precision, c.scale, c.is_nullable, c.is_identity,
       c.collation_name, dc.definition AS default_definition, cc.definition AS computed_definition
FROM sys.columns c
JOIN sys.objects o ON o.object_id = c.object_id AND o.is_ms_shipped = 0
JOIN sys.types t ON t.user_type_id = c.user_type_id
LEFT JOIN sys.types bt ON bt.user_type_id = t.system_type_id
LEFT JOIN sys.default_constraints dc ON dc.object_id = c.default_object_id
LEFT JOIN sys.computed_columns cc ON cc.object_id = c.object_id AND cc.column_id = c.column_id
WHERE o.type IN ('U','V')
ORDER BY c.object_id, c.column_id;
"""

FETCH_PRIMARY_KEYS = """
SELECT i.object_id, col.name AS column_name, ic.key_ordinal
FROM sys.indexes i
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns col ON col.object_id = ic.object_id AND col.column_id = ic.column_id
WHERE i.is_primary_key = 1
ORDER BY i.object_id, ic.key_ordinal;
"""

FETCH_FOREIGN_KEYS = """
SELECT fk.parent_object_id AS object_id, fk.name AS fk_name,
       OBJECT_SCHEMA_NAME(fk.referenced_object_id) + '.' + OBJECT_NAME(fk.referenced_object_id) AS to_table,
       cpa.name AS from_column, cre.name AS to_column, fkc.constraint_column_id AS ord
FROM sys.foreign_keys fk
JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
JOIN sys.columns cpa ON cpa.object_id = fkc.parent_object_id AND cpa.column_id = fkc.parent_column_id
JOIN sys.columns cre ON cre.object_id = fkc.referenced_object_id AND cre.column_id = fkc.referenced_column_id
ORDER BY fk.parent_object_id, fk.name, fkc.constraint_column_id;
"""

FETCH_CHECK_CONSTRAINTS = """
SELECT parent_object_id AS object_id, name, definition
FROM sys.check_constraints;
"""

FETCH_INDEXES = """
SELECT i.object_id, i.name AS index_name, i.is_unique,
       col.name AS column_name, ic.key_ordinal
FROM sys.indexes i
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns col ON col.object_id = ic.object_id AND col.column_id = ic.column_id
JOIN sys.objects o ON o.object_id = i.object_id AND o.is_ms_shipped = 0
WHERE i.is_primary_key = 0 AND i.type > 0
ORDER BY i.object_id, i.name, ic.key_ordinal;
"""

# Satır sayısı + veri boyutu (veri OKUMADAN, sadece istatistik DMV — design/05).
FETCH_TABLE_STATS = """
SELECT p.object_id,
       SUM(CASE WHEN p.index_id IN (0,1) THEN p.row_count ELSE 0 END) AS row_count,
       SUM(p.reserved_page_count) * 8.0 / 1024.0 AS data_size_mb
FROM sys.dm_db_partition_stats p
GROUP BY p.object_id;
"""
