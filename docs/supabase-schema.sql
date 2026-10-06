-- Supabase SQL Editor에서 실행하는 읽기 전용 구조 조사.
-- 사용자 데이터는 조회하지 않으며, JSON 한 행으로 반환해 100행 제한을 피합니다.
SELECT jsonb_build_object(
  'columns', (
    SELECT jsonb_agg(to_jsonb(c) ORDER BY c.table_name, c.ordinal_position)
    FROM (
      SELECT table_name, ordinal_position, column_name, data_type, udt_name,
             is_nullable, column_default IS NOT NULL AS has_default, is_identity
      FROM information_schema.columns WHERE table_schema = 'public'
    ) c
  ),
  'constraints', (
    SELECT jsonb_agg(jsonb_build_object('table_name', t.relname, 'name', c.conname,
           'definition', pg_get_constraintdef(c.oid)) ORDER BY t.relname, c.conname)
    FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace WHERE n.nspname = 'public'
  ),
  'indexes', (
    SELECT jsonb_agg(to_jsonb(i)) FROM (
      SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'
    ) i
  ),
  'row_security', (
    SELECT jsonb_agg(jsonb_build_object('table_name', c.relname,
           'enabled', c.relrowsecurity, 'forced', c.relforcerowsecurity))
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r'
  ),
  'policies', (
    SELECT jsonb_agg(to_jsonb(p)) FROM (
      SELECT tablename, policyname, permissive, roles, cmd, qual, with_check
      FROM pg_policies WHERE schemaname = 'public'
    ) p
  )
) AS database_schema;
