// 진입점 관례(`src/main`)에는 맞는다 — 즉 진입점은 찾힌다.
// 그런데 스타일은 우리가 해석하지 못하는 경로로 실린다: Vite 가상 모듈처럼
// 파일 시스템에 대응물이 없는 지정자거나, 번들러 설정이 주입하는 css.
// 그래프는 만들어졌지만 감사 대상 스타일시트에 한 개도 닿지 못한 상태 —
// 이건 "파일이 죽었다"가 아니라 "우리가 그래프를 못 만들었다"이다.
import "virtual:app-styles";

export function boot() {
  document.documentElement.dataset.booted = "1";
}
