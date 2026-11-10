-- MES 정합성 불변식. 행마다 (검사 이름, 위반 건수)를 돌려주고 모두 0이어야 한다
-- 대상은 이번 부하 테스트가 만든 Lot(lot_no에 RUN_ID 포함)이다. bench.py가 @run을 정해서 실행한다
--   SET @run = '%-<RUN_ID>-%';
-- 자동 Hold 누락은 이항검정이 필요해 bench.py가 run_lot_counts 결과로 계산한다

-- 1. 이벤트 체인: 첫 이벤트는 NULL→RECEIVED, 이후 from은 직전 to와 같아야 한다 (중복·누락 전이 검출)
SELECT 'event_chain_broken', COUNT(DISTINCT e.inspection_id)
FROM (
    SELECT ev.inspection_id, ev.from_status, ev.to_status,
           LAG(ev.to_status) OVER (PARTITION BY ev.inspection_id ORDER BY ev.id) AS prev_to,
           ROW_NUMBER() OVER (PARTITION BY ev.inspection_id ORDER BY ev.id) AS seq
    FROM inspection_event ev
    JOIN inspection i ON i.id = ev.inspection_id
    JOIN wafer w ON w.id = i.wafer_id
    JOIN lot l ON l.id = w.lot_id
    WHERE l.lot_no LIKE @run
) e
WHERE (e.seq = 1 AND NOT (e.from_status IS NULL AND e.to_status = 'RECEIVED'))
   OR (e.seq > 1 AND NOT (e.from_status <=> e.prev_to));

-- 2. 검사 상태는 마지막 이벤트의 to와 같아야 하고, 이벤트 없는 검사는 없어야 한다
SELECT 'inspection_status_mismatch', COUNT(*)
FROM inspection i
JOIN wafer w ON w.id = i.wafer_id
JOIN lot l ON l.id = w.lot_id
LEFT JOIN inspection_event last ON last.id = (SELECT MAX(id) FROM inspection_event WHERE inspection_id = i.id)
WHERE l.lot_no LIKE @run AND (last.id IS NULL OR last.to_status <> i.status);

-- 3. 웨이퍼 상태는 최신 검사 상태에서 정해진다. 검사가 없으면 WAITING
SELECT 'wafer_status_mismatch', COUNT(*)
FROM wafer w
JOIN lot l ON l.id = w.lot_id
LEFT JOIN inspection i ON i.id = (SELECT MAX(id) FROM inspection WHERE wafer_id = w.id)
WHERE l.lot_no LIKE @run
  AND w.status <> CASE
        WHEN i.id IS NULL THEN 'WAITING'
        WHEN i.status IN ('RECEIVED', 'UPLOADED', 'INFERRING') THEN 'INSPECTING'
        WHEN i.status = 'NEED_REVIEW' THEN 'REVIEW'
        WHEN i.status = 'FAILED' THEN 'FAILED'
        WHEN LOWER(i.final_prediction) = 'none' THEN 'PASS'
        ELSE 'DEFECT' END;

-- 4. 한 웨이퍼에 진행 중인 검사는 하나뿐이다
SELECT 'duplicate_active_inspection', COUNT(*)
FROM (
    SELECT i.wafer_id
    FROM inspection i
    JOIN wafer w ON w.id = i.wafer_id
    JOIN lot l ON l.id = w.lot_id
    WHERE l.lot_no LIKE @run AND i.status IN ('RECEIVED', 'UPLOADED', 'INFERRING', 'NEED_REVIEW')
    GROUP BY i.wafer_id
    HAVING COUNT(*) > 1
) d;

-- 5. 부하가 끝난 뒤 처리 중 상태로 남은 검사 (요청은 끝났는데 결과가 기록되지 않음)
SELECT 'stuck_in_progress', COUNT(*)
FROM inspection i
JOIN wafer w ON w.id = i.wafer_id
JOIN lot l ON l.id = w.lot_id
WHERE l.lot_no LIKE @run AND i.status IN ('RECEIVED', 'UPLOADED', 'INFERRING');

-- 6. COMPLETED Lot에는 미완료 웨이퍼가 없어야 한다
SELECT 'completed_lot_with_unfinished_wafer', COUNT(DISTINCT l.id)
FROM lot l
JOIN wafer w ON w.lot_id = l.id
WHERE l.lot_no LIKE @run AND l.status = 'COMPLETED' AND w.status NOT IN ('PASS', 'DEFECT');

-- 7. 웨이퍼가 모두 판정됐는데 진행 중으로 남은 Lot (Hold는 제외)
SELECT 'finished_lot_not_completed', COUNT(*)
FROM lot l
WHERE l.lot_no LIKE @run AND l.status = 'IN_PROGRESS'
  AND NOT EXISTS (SELECT 1 FROM wafer w WHERE w.lot_id = l.id AND w.status NOT IN ('PASS', 'DEFECT'));

-- 8. Lot 완료 수량: COMPLETED Lot의 웨이퍼 수 = 판정 확정(DONE) 검사가 있는 웨이퍼 수
SELECT 'completed_lot_count_mismatch', COUNT(*)
FROM (
    SELECT l.id, COUNT(*) AS wafers,
           SUM(EXISTS (SELECT 1 FROM inspection i WHERE i.wafer_id = w.id AND i.status = 'DONE')) AS done
    FROM lot l
    JOIN wafer w ON w.lot_id = l.id
    WHERE l.lot_no LIKE @run AND l.status = 'COMPLETED'
    GROUP BY l.id
) c
WHERE c.wafers <> c.done;

-- 9. 공정 이력: 웨이퍼·공정마다 한 행, 열린 이력은 웨이퍼마다 최대 하나
SELECT 'process_history_duplicate', COUNT(*)
FROM (
    SELECT h.wafer_id, h.step_id
    FROM wafer_process_history h
    JOIN wafer w ON w.id = h.wafer_id
    JOIN lot l ON l.id = w.lot_id
    WHERE l.lot_no LIKE @run
    GROUP BY h.wafer_id, h.step_id
    HAVING COUNT(*) > 1
) d;

SELECT 'process_history_multiple_open', COUNT(*)
FROM (
    SELECT h.wafer_id
    FROM wafer_process_history h
    JOIN wafer w ON w.id = h.wafer_id
    JOIN lot l ON l.id = w.lot_id
    WHERE l.lot_no LIKE @run AND h.ended_at IS NULL
    GROUP BY h.wafer_id
    HAVING COUNT(*) > 1
) d;
