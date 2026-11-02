// Istio Outlier Detection 실험 부하 (RUNBOOK 4.15): backend /ai/predict에 일정한 RPS로 이미지 업로드 → AI 추론.
// constant-arrival-rate라 응답이 느려져도 요청 속도는 유지된다 (느려지면 VU를 더 쓴다).
//   k6 run -e BASE_URL=https://api.wafer.local -e SCENARIO=A --summary-export results/A.json loadtest/ai-predict.js
// 환경변수: BASE_URL, RATE(초당 요청, 기본 20), DURATION(기본 5m), SCENARIO(결과 태그), INSECURE=true(사설 CA를 맥에 등록하지 않았을 때)
import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'https://api.wafer.local';
const RATE = Number(__ENV.RATE || 20);
const DURATION = __ENV.DURATION || '5m';
const image = open('./wafer-sample.png', 'b');

export const options = {
  insecureSkipTLSVerify: __ENV.INSECURE === 'true',
  scenarios: {
    predict: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: 40,
      maxVUs: 200,
    },
  },
  tags: { experiment: __ENV.SCENARIO || 'unknown' },
  summaryTrendStats: ['avg', 'p(50)', 'p(95)', 'p(99)', 'max'],
};

export default function () {
  const res = http.post(
    `${BASE_URL}/ai/predict`,
    { file: http.file(image, 'wafer-sample.png', 'image/png') },
    { timeout: '30s', tags: { name: 'POST /ai/predict' } },
  );
  check(res, { 'status 200': (r) => r.status === 200 });
}
