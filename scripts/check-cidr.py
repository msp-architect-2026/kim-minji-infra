#!/usr/bin/env python3
"""Calico Pod CIDR(렌더된 Installation의 ipPools)이 노드 대역(values의 nodeCIDRs)·k3s Service 대역과 겹치면 실패한다.

Pod CIDR이 노드 대역과 겹치면 Calico가 노드 IP를 Pod 주소로 보고 VXLAN으로 보내 클러스터 통신이 끊긴다
(초기 구축 때 전면 재구축 원인). 표준 라이브러리만 쓰려고 YAML은 정규식으로 읽는다 (block 스타일 리스트만 지원).
사용법: check-cidr.py <렌더된 calico.yaml> <values 파일>...
"""
import ipaddress
import re
import sys

SERVICE_CIDR = ipaddress.ip_network("10.43.0.0/16")  # k3s 기본 --service-cidr


def net(text, where):
    try:
        return ipaddress.ip_network(text)
    except ValueError as e:
        sys.exit(f"잘못된 CIDR {text!r} ({where}): {e}")


rendered, values_files = sys.argv[1], sys.argv[2:]

# Installation 문서 안의 cidr만 본다
docs = re.split(r"^---\s*$", open(rendered).read(), flags=re.M)
inst = [d for d in docs if re.search(r"^kind:\s*Installation\s*$", d, re.M)]
if not inst:
    sys.exit(f"Installation 리소스를 찾지 못함: {rendered}")
pod_cidrs = [net(c, "Installation") for c in re.findall(r"^\s*-?\s*cidr:\s*['\"]?([0-9a-fA-F.:/]+)", inst[0], re.M)]

node_cidrs = None
for path in values_files:  # 뒤 파일이 앞 파일을 덮어쓴다 (helm -f 순서와 같게)
    text = open(path).read()
    m = re.search(r"^nodeCIDRs:[ \t]*(\S.*)?\n((?:[ \t]+(?:-.*|#.*)\n?|[ \t]*\n)*)", text, re.M)
    if not m:
        continue
    if m.group(1) and not m.group(1).startswith("#"):
        sys.exit(f"{path}: nodeCIDRs는 block 스타일 리스트로 적는다 (현재: {m.group(1)!r})")
    node_cidrs = [net(c, path) for c in re.findall(r"^[ \t]+-\s*['\"]?([0-9a-fA-F.:/]+)", m.group(2), re.M)]

if not pod_cidrs:
    sys.exit(f"Installation ipPools cidr를 찾지 못함: {rendered}")
if not node_cidrs:
    sys.exit("values에 nodeCIDRs가 없거나 비어 있음")

bad = [(p, n) for p in pod_cidrs for n in node_cidrs + [SERVICE_CIDR] if p.overlaps(n)]
for p, n in bad:
    print(f"Pod CIDR {p} 이(가) {'Service 대역' if n == SERVICE_CIDR else '노드 대역'} {n} 과(와) 겹침", file=sys.stderr)
if bad:
    sys.exit(1)
print(f"CIDR 검사 통과: Pod {', '.join(map(str, pod_cidrs))} / 노드 {', '.join(map(str, node_cidrs))} / Service {SERVICE_CIDR}")
