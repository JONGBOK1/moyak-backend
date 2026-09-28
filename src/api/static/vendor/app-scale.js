/**
 * 피그마 디자인 그대로(402x874 기준 화면) 만들어진 /app 페이지들을 폰이든 PC 웹브라우저든
 * 화면 크기에 맞춰 통째로 축소/확대해서 보여준다. 각 페이지 내부 CSS(절대좌표 등)는
 * 전혀 건드리지 않고, body의 첫 번째 자식(디자인 루트 래퍼 div)을 그대로 스케일링하는
 * 방식이라 모든 /app 페이지에 공통으로 적용 가능하다.
 *
 * 디자인 기준 크기는 기본 402x874(/app)이고, 스크립트 태그의 data-width/data-height로
 * 바꿀 수 있다 — 키오스크(/kiosk) 페이지는 1080x1920으로 같은 스크립트를 쓴다:
 *   <script src="/static/vendor/app-scale.js" data-width="1080" data-height="1920" data-match-bg></script>
 * data-match-bg가 있으면 화면 가장자리 여백을 디자인 배경색으로 채운다.
 *
 * 스케일 배율은 "한 화면 분량"인 디자인 기준 크기로 계산해서, 대부분의 화면(로그인,
 * 챗봇, 메인홈 등)은 스크롤 없이 화면에 딱 맞게 줄어든다.
 * - 콘텐츠가 뷰포트보다 짧으면(대부분의 경우, 특히 PC처럼 화면이 큰 경우) 가운데
 *   정렬해서 화면 한복판에 뜨게 한다.
 * - 콘텐츠가 뷰포트보다 길면(카드가 여러 개 쌓이는 목록 화면 등) 위쪽부터 채우고
 *   초과분만큼 세로 스크롤이 생겨서 잘리지 않고 끝까지 볼 수 있다.
 */
(function () {
  var script = document.currentScript;
  var DESIGN_WIDTH = Number(script && script.dataset.width) || 402;
  var DESIGN_HEIGHT = Number(script && script.dataset.height) || 874;
  var MATCH_BG = !!(script && script.hasAttribute("data-match-bg"));

  function fit() {
    var root = document.body.firstElementChild;
    if (!root) return;

    root.style.width = DESIGN_WIDTH + "px";
    root.style.minWidth = DESIGN_WIDTH + "px";
    root.style.maxWidth = DESIGN_WIDTH + "px";
    root.style.position = "absolute";
    root.style.transformOrigin = "top left";
    root.style.transform = "none"; // 실제(스케일 전) 콘텐츠 높이를 재기 위해 잠깐 해제

    if (MATCH_BG) {
      var bg = getComputedStyle(root).backgroundColor;
      if (bg && bg !== "rgba(0, 0, 0, 0)" && bg !== "transparent") {
        document.documentElement.style.background = bg;
        document.body.style.background = bg;
      }
    }

    var scale = Math.min(window.innerWidth / DESIGN_WIDTH, window.innerHeight / DESIGN_HEIGHT);
    var naturalHeight = root.scrollHeight;

    root.style.transform = "scale(" + scale + ")";

    var scaledWidth = DESIGN_WIDTH * scale;
    var scaledHeight = naturalHeight * scale;

    root.style.left = Math.max(0, (window.innerWidth - scaledWidth) / 2) + "px";

    if (scaledHeight < window.innerHeight) {
      root.style.top = (window.innerHeight - scaledHeight) / 2 + "px";
      document.body.style.height = window.innerHeight + "px";
    } else {
      root.style.top = "0px";
      document.body.style.height = scaledHeight + "px";
    }
  }

  window.addEventListener("resize", fit);
  window.addEventListener("orientationchange", fit);
  document.addEventListener("DOMContentLoaded", fit);
  window.addEventListener("load", fit);
  fit();
})();
