-- MES 부하 테스트 시드: Lot 8,000개, 웨이퍼 200,000장, 공정 이력 800,000행, 검사 200,000건, 검사 이벤트 800,000행 (90일 분포)
-- 재현 가능하도록 난수 대신 id 기반 식으로 값을 정한다. 빈 DB에 Flyway 적용 후 실행한다
SET SESSION cte_max_recursion_depth = 300000;
SET SESSION foreign_key_checks = 0;
SET SESSION unique_checks = 0;

INSERT INTO lot (id, lot_no, product, status, version, created_at)
WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 8000)
SELECT i, CONCAT('SEED-', LPAD(i, 6, '0')), 'DRAM-16G', 'COMPLETED', 0,
       NOW(6) - INTERVAL MOD(i * 7919, 129600) MINUTE
FROM n;

INSERT INTO wafer (id, lot_id, slot_no, status, version)
WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 200000)
SELECT i, FLOOR((i - 1) / 25) + 1, MOD(i - 1, 25) + 1,
       IF(MOD(i * 31, 100) < 90, 'PASS', 'DEFECT'), 0
FROM n;

INSERT INTO inspection (id, wafer_id, filename, image_key, status, prediction, final_prediction, confidence,
                        model_version, trace_id, attempt, version, created_at, updated_at)
SELECT w.id, w.id, CONCAT('seed-', w.id, '.png'), CONCAT('seed/', w.id, '.png'), 'DONE', p.label, p.label, 0.95,
       'wafer-cnn-v1', LPAD(HEX(w.id), 32, '0'), 1, 0, l.created_at, l.created_at
FROM wafer w
JOIN lot l ON l.id = w.lot_id
JOIN LATERAL (
    SELECT IF(w.status = 'PASS', 'none',
              ELT(MOD(w.id, 8) + 1, 'Center', 'Donut', 'Edge-Loc', 'Edge-Ring', 'Loc', 'Near-full', 'Random', 'Scratch')) AS label
) p ON TRUE;

INSERT INTO inspection_event (inspection_id, from_status, to_status, trace_id, occurred_at)
SELECT i.id, s.from_status, s.to_status, i.trace_id, i.created_at
FROM inspection i
JOIN (
    SELECT CAST(NULL AS CHAR(20)) AS from_status, 'RECEIVED' AS to_status
    UNION ALL SELECT 'RECEIVED', 'UPLOADED'
    UNION ALL SELECT 'UPLOADED', 'INFERRING'
    UNION ALL SELECT 'INFERRING', 'DONE'
) s;

-- 공정 순서대로 설비를 Lot 단위로 고른다 (step id 1~4, equipment id: PHOTO 1~2, ETCH 3~5, DEPO 6~7, CMP 8~9)
INSERT INTO wafer_process_history (wafer_id, step_id, equipment_id, recipe, started_at, ended_at)
SELECT w.id, s.step_id, s.base + MOD(w.lot_id * s.prime, s.tools), CONCAT('R', s.step_id),
       l.created_at + INTERVAL s.step_id * 30 MINUTE, l.created_at + INTERVAL (s.step_id + 1) * 30 MINUTE
FROM wafer w
JOIN lot l ON l.id = w.lot_id
JOIN (
    SELECT 1 AS step_id, 1 AS base, 2 AS tools, 7 AS prime
    UNION ALL SELECT 2, 3, 3, 11
    UNION ALL SELECT 3, 6, 2, 13
    UNION ALL SELECT 4, 8, 2, 17
) s;

SET SESSION foreign_key_checks = 1;
SET SESSION unique_checks = 1;
ANALYZE TABLE lot, wafer, inspection, inspection_event, wafer_process_history;
