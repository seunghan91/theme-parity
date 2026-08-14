// 두 번째 진입점. 스타일을 별칭(`@/`)으로 부른다 — 우리는 별칭 설정을 읽지 않으므로
// 이 지정자는 해석되지 않는다. 없는 셈 치면 멀쩡한 파일이 고아로 보고된다.
// 파일명이 일치하면 도달한 것으로 쳐 주는 구제 경로가 여기서 검증된다.
import "@/css/reach-aliased.css";

export function bootAliased() {
  document.documentElement.dataset.aliased = "1";
}
