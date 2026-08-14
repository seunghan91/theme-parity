// Storybook 전용 로더. 앱 진입점이 아니다 — 여기서만 import 되는 스타일시트는
// 프로덕션 화면에 존재하지 않는다.
import "../app/frontend/css/reach-sb.css";

export default { parameters: {} };
