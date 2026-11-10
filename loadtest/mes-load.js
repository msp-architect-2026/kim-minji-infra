// MES API 부하 테스트. 일정한 도착률로 요청해 응답이 느려져도 부하가 줄지 않게 한다
//   k6 run -e BASE_URL=http://localhost:8080 -e SCENARIO=dashboard -e RATE=20 -e DURATION=30s --summary-export results/x.json loadtest/mes-load.js
// SCENARIO: dashboard(대시보드·통계) | spc(관리도·설비 분석) | lots(Lot 조회·실패 현황) | inspect(웨이퍼 검사) | track(TrackIn/Out)
import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const SCENARIO = __ENV.SCENARIO || 'dashboard';
const RATE = Number(__ENV.RATE || 20);
const DURATION = __ENV.DURATION || '30s';
const SEEDED_LOTS = Number(__ENV.SEEDED_LOTS || 8000);
const TIMEOUT = '30s';
const JSON_HEADERS = { 'Content-Type': 'application/json' };
// 실제 수율처럼 대부분 양품으로 보낸다. 결함만 보내면 자동 Hold 규칙(결함률 30%)에 걸려 나머지 검사가 업무 규칙대로 거부된다
const DEFECT_RATIO = Number(__ENV.DEFECT_RATIO || 0.08);
const passImage = open('./wafer-map-pass.png', 'b');
const defectImage = open('./wafer-map-defect.png', 'b');

const ENDPOINTS = {
  dashboard: ['GET /wafer/stats', 'GET /wafer/stats/daily', 'GET /wafer/stats/defect-distribution', 'GET /wafer/records/pages'],
  spc: ['GET /spc/p-chart', 'GET /spc/commonality'],
  lots: ['GET /lots', 'GET /lots/{id}', 'GET /failures/summary'],
  inspect: ['POST inspection'],
  track: ['POST track-in', 'POST track-out'],
};

const thresholds = { dropped_iterations: [] };
for (const name of ENDPOINTS[SCENARIO]) {
  thresholds[`http_req_duration{name:${name}}`] = [];
  thresholds[`http_req_failed{name:${name}}`] = [];
}

export const options = {
  scenarios: {
    load: { executor: 'constant-arrival-rate', rate: RATE, timeUnit: '1s', duration: DURATION, preAllocatedVUs: 20, maxVUs: 400 },
  },
  summaryTrendStats: ['avg', 'p(50)', 'p(95)', 'p(99)', 'max'],
  thresholds,
  setupTimeout: '300s',
};

function get(path, name) {
  const res = http.get(`${BASE_URL}${path}`, { timeout: TIMEOUT, tags: { name } });
  check(res, { 'status 200': (r) => r.status === 200 });
}

function createLots(count, waferCount, prefix) {
  const ids = [];
  for (let i = 0; i < count; i++) {
    const body = JSON.stringify({ lotNo: `${prefix}-${Date.now()}-${i}`, product: 'LOAD', waferCount });
    const res = http.post(`${BASE_URL}/lots`, body, { headers: JSON_HEADERS, timeout: TIMEOUT });
    if (res.status !== 201) throw new Error(`Lot 생성 실패: ${res.status} ${res.body}`);
    ids.push(res.json('id'));
  }
  return ids;
}

export function setup() {
  const total = RATE * parseInt(DURATION, 10);
  if (SCENARIO === 'inspect') return { lots: createLots(Math.ceil(total / 25) + 1, 25, 'INS') };
  if (SCENARIO === 'track') return { lots: createLots(Math.ceil(total / 2) + 1, 25, 'TRK') };
  return {};
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
  // 슬롯이 겹치지 않도록 반복 번호로 Lot과 슬롯을 정한다
  inspect(data, iteration) {
    const lot = data.lots[Math.floor(iteration / 25)];
    const res = http.post(
      `${BASE_URL}/lots/${lot}/wafers/${(iteration % 25) + 1}/inspections`,
      { file: http.file(Math.random() < DEFECT_RATIO ? defectImage : passImage, 'wafer.png', 'image/png') },
      { timeout: TIMEOUT, tags: { name: 'POST inspection' } },
    );
    check(res, { 'status 200': (r) => r.status === 200 });
  },
  track(data, iteration) {
    const lot = data.lots[Math.floor(iteration / 2)];
    const trackIn = iteration % 2 === 0;
    const body = trackIn
      ? JSON.stringify({ stepCode: 'PHOTO', equipmentCode: 'PHOTO-01', recipe: 'R1' })
      : JSON.stringify({ stepCode: 'PHOTO' });
    const res = http.post(`${BASE_URL}/lots/${lot}/${trackIn ? 'track-in' : 'track-out'}`, body, {
      headers: JSON_HEADERS, timeout: TIMEOUT, tags: { name: trackIn ? 'POST track-in' : 'POST track-out' },
    });
    check(res, { 'status 2xx/409': (r) => r.status === 200 || r.status === 409 });
  },
};

import exec from 'k6/execution';

export default function (data) {
  scenarios[SCENARIO](data, exec.scenario.iterationInTest);
}
