// Vite Ruby 관례: `entrypoints/` 아래가 앱 진입점이다.
// 여기서 시작한 import 그래프에 닿지 않는 스타일시트는 번들에 실리지 않는다.
import "../css/reach-app.css";

export function boot() {
  document.documentElement.dataset.booted = "1";
}
