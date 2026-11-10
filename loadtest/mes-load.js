// MES API 부하 테스트. 일정한 도착률로 요청해 응답이 느려져도 부하가 줄지 않게 한다
//   k6 run -e BASE_URL=http://localhost:8080 -e SCENARIO=dashboard -e RATE=20 -e DURATION=30s --summary-export results/x.json loadtest/mes-load.js
// SCENARIO: dashboard(대시보드·통계) | spc(관리도·설비 분석) | lots(Lot 조회·실패 현황)
//           inspect(웨이퍼 검사) | track(TrackIn/Out) | pool(같은 Lot에 몰린 검사 + 다른 Lot의 저부하 검사)
// DURATION은 초 단위(예: 60s)로 준다. 측정 자동화는 bench.py
import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';
import exec from 'k6/execution';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const SCENARIO = __ENV.SCENARIO || 'dashboard';
const RATE = Number(__ENV.RATE || 20);
const DURATION = __ENV.DURATION || '30s';
const SEEDED_LOTS = Number(__ENV.SEEDED_LOTS || 8000);
const RUN_ID = __ENV.RUN_ID || `${Date.now()}`;
// 동시에 검사를 받는 Lot 수. 1이면 연속된 요청이 모두 한 Lot에 몰린다(Lot 잠금 경합 최대)
const HOT_LOTS = Number(__ENV.HOT_LOTS || 1);
const WAFERS = Number(__ENV.WAFERS || 25);
// pool 시나리오에서 다른 Lot으로 보내는 저부하 검사 도착률
const CALM_RATE = Number(__ENV.CALM_RATE || 2);
const TIMEOUT = '30s';
const JSON_HEADERS = { 'Content-Type': 'application/json' };
// 실제 수율처럼 대부분 양품으로 보낸다. 결함만 보내면 자동 Hold에 걸려 나머지 검사가 업무 규칙대로 거부된다
const DEFECT_RATIO = Number(__ENV.DEFECT_RATIO || 0.08);
// bench.py가 측정 전에 만든 Lot 목록. 없으면 setup에서 만든다(이때는 Lot 생성 SQL이 측정에 섞인다)
const PRESET_LOTS = __ENV.LOTS_FILE ? JSON.parse(open(__ENV.LOTS_FILE)) : null;
const passImage = open('./wafer-map-pass.png', 'b');
const defectImage = open('./wafer-map-defect.png', 'b');

// Hold된 Lot의 검사 거부(409)는 업무 규칙대로의 응답이므로 실패로 세지 않고 따로 센다
const statusCodes = new Counter('status_codes');
const STATUS_TAGS = ['200', '409', '4xx', '5xx', '0'];

const ENDPOINTS = {
  dashboard: ['GET /wafer/stats', 'GET /wafer/stats/daily', 'GET /wafer/stats/defect-distribution', 'GET /wafer/records/pages'],
  spc: ['GET /spc/p-chart', 'GET /spc/commonality'],
  lots: ['GET /lots', 'GET /lots/{id}', 'GET /failures/summary'],
  inspect: ['POST inspection'],
  track: ['POST track-in', 'POST track-out'],
  pool: ['POST inspection hot', 'POST inspection calm'],
};

const thresholds = { dropped_iterations: [] };
for (const name of ENDPOINTS[SCENARIO]) {
  thresholds[`http_req_duration{name:${name}}`] = [];
  thresholds[`http_req_failed{name:${name}}`] = [];
  thresholds[`http_reqs{name:${name}}`] = [];
  for (const status of STATUS_TAGS) thresholds[`status_codes{name:${name},status:${status}}`] = [];
}

const seconds = parseInt(DURATION, 10);
const load = (exec, rate) => ({
  executor: 'constant-arrival-rate', exec, rate, timeUnit: '1s', duration: DURATION, preAllocatedVUs: 20, maxVUs: 400,
});

export const options = {
  scenarios: SCENARIO === 'pool'
    ? { hot: load('hot', RATE), calm: load('calm', CALM_RATE) }
    : { load: load('run', RATE) },
  summaryTrendStats: ['avg', 'p(50)', 'p(95)', 'p(99)', 'max'],
  thresholds,
  setupTimeout: '300s',
};

function statusTag(code) {
  if (code === 200 || code === 409 || code === 0) return String(code);
  return code >= 500 ? '5xx' : '4xx';
}

function record(res, name) {
  statusCodes.add(1, { name, status: statusTag(res.status) });
}

function get(path, name) {
  const res = http.get(`${BASE_URL}${path}`, { timeout: TIMEOUT, tags: { name } });
  record(res, name);
  check(res, { 'status 200': (r) => r.status === 200 });
}

function createLots(count, waferCount, prefix) {
  const ids = [];
  for (let i = 0; i < count; i++) {
    const body = JSON.stringify({ lotNo: `${prefix}-${RUN_ID}-${i}`, product: 'LOAD', waferCount });
    const res = http.post(`${BASE_URL}/lots`, body, { headers: JSON_HEADERS, timeout: TIMEOUT });
    if (res.status !== 201) throw new Error(`Lot 생성 실패: ${res.status} ${res.body}`);
    ids.push(res.json('id'));
  }
  return ids;
}

// 반복 번호를 (Lot, 슬롯)으로 바꾼다. hotLots개 Lot에 슬롯을 번갈아 보내고, 다 차면 다음 묶음으로 넘어간다
function slotOf(lots, iteration, hotLots) {
  const perGroup = WAFERS * hotLots;
  const group = Math.floor(iteration / perGroup);
  const within = iteration % perGroup;
  return { lot: lots[group * hotLots + (within % hotLots)], slot: Math.floor(within / hotLots) + 1 };
}

function lotsFor(rate, hotLots) {
  const perGroup = WAFERS * hotLots;
  return Math.ceil((rate * seconds) / perGroup) * hotLots + hotLots;
}

export function setup() {
  if (PRESET_LOTS) return PRESET_LOTS;
  if (SCENARIO === 'inspect') return { lots: createLots(lotsFor(RATE, HOT_LOTS), WAFERS, 'INS') };
  if (SCENARIO === 'track') return { lots: createLots(Math.ceil((RATE * seconds) / 2) + 1, WAFERS, 'TRK') };
  // calm은 Lot마다 한 장씩만 보내 잠금 경합이 없게 한다
  if (SCENARIO === 'pool') return { hot: createLots(lotsFor(RATE, 1), WAFERS, 'HOT'), calm: createLots(lotsFor(CALM_RATE, 50), WAFERS, 'CALM') };
  return {};
}

// 파일명은 MinIO 키에 들어가 AI stub이 결함 여부를 정하는 데 쓴다. 실제 AI는 이미지로 판정한다
function inspect(lots, iteration, hotLots, name) {
  const { lot, slot } = slotOf(lots, iteration, hotLots);
  const defect = Math.random() < DEFECT_RATIO;
  const key = `${RUN_ID}-${lot}-${slot}`;
  const res = http.post(
    `${BASE_URL}/lots/${lot}/wafers/${slot}/inspections`,
    { file: http.file(defect ? defectImage : passImage, defect ? 'defect.png' : 'pass.png', 'image/png') },
    { timeout: TIMEOUT, tags: { name }, headers: { 'Idempotency-Key': key }, responseCallback: http.expectedStatuses(200, 409) },
  );
  record(res, name);
  // bench.py가 수락된 요청을 DB와 대조한다
  if (res.status === 200) console.log(`ACCEPTED ${key} ${res.json('id')}`);
  check(res, { 'status 200/409': (r) => r.status === 200 || r.status === 409 });
}

const scenarios = {
  dashboard() {
    const pick = Math.floor(Math.random() * 4);
    if (pick === 0) get('/wafer/stats', 'GET /wafer/stats');
    if (pick === 1) get('/wafer/stats/daily?days=7', 'GET /wafer/stats/daily');
    if (pick === 2) get('/wafer/stats/defect-distribution', 'GET /wafer/stats/defect-distribution');
    if (pick === 3) get('/wafer/records/pages?search=&page=0&size=8', 'GET /wafer/records/pages');
  },
  spc() {
    if (Math.random() < 0.5) get('/spc/p-chart?days=14', 'GET /spc/p-chart');
    else get('/spc/commonality?days=14', 'GET /spc/commonality');
  },
  lots() {
    const pick = Math.random();
    if (pick < 0.4) get('/lots?page=0&size=20', 'GET /lots');
    else if (pick < 0.9) get(`/lots/${1 + Math.floor(Math.random() * SEEDED_LOTS)}`, 'GET /lots/{id}');
    else get('/failures/summary?hours=24', 'GET /failures/summary');
  },
  inspect(data, iteration) {
    inspect(data.lots, iteration, HOT_LOTS, 'POST inspection');
  },
  track(data, iteration) {
    const lot = data.lots[Math.floor(iteration / 2)];
    const trackIn = iteration % 2 === 0;
    const name = trackIn ? 'POST track-in' : 'POST track-out';
    const body = trackIn
      ? JSON.stringify({ stepCode: 'PHOTO', equipmentCode: 'PHOTO-01', recipe: 'R1' })
      : JSON.stringify({ stepCode: 'PHOTO' });
    const res = http.post(`${BASE_URL}/lots/${lot}/${trackIn ? 'track-in' : 'track-out'}`, body, {
      headers: JSON_HEADERS, timeout: TIMEOUT, tags: { name }, responseCallback: http.expectedStatuses(200, 409),
    });
    record(res, name);
    check(res, { 'status 2xx/409': (r) => r.status === 200 || r.status === 409 });
  },
};

export function run(data) {
  scenarios[SCENARIO](data, exec.scenario.iterationInTest);
}

export function hot(data) {
  inspect(data.hot, exec.scenario.iterationInTest, 1, 'POST inspection hot');
}

export function calm(data) {
  inspect(data.calm, exec.scenario.iterationInTest, 50, 'POST inspection calm');
}
