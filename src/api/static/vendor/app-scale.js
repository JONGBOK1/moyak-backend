/**
 * 피그마 디자인 그대로(402px 폭 고정) 만들어진 /app 페이지들을 실제 폰 화면 너비에 맞춰
 * 통째로 축소/확대해서 보여준다. 각 페이지 내부 CSS(절대좌표 등)는 전혀 건드리지 않고,
 * body의 첫 번째 자식(디자인 루트 래퍼 div)을 그대로 스케일링하는 방식이라 모든
 * /app 페이지에 공통으로 적용 가능하다.
 *
 * 높이는 페이지마다 실제 콘텐츠 양에 따라 다르므로(짧은 안내 화면 vs 카드가 여러 개
 * 쌓이는 목록 화면) 고정값으로 자르지 않고, 스케일된 실제 높이만큼 body 높이를 잡아줘서
 * 화면보다 긴 페이지는 끝까지 스크롤해서 볼 수 있게 한다.
 */
(function () {
  var DESIGN_WIDTH = 402;

  function fit() {
    var root = document.body.firstElementChild;
    if (!root) return;

    root.style.width = DESIGN_WIDTH + "px";
    root.style.minWidth = DESIGN_WIDTH + "px";
    root.style.maxWidth = DESIGN_WIDTH + "px";
    root.style.position = "absolute";
    root.style.top = "0";
    root.style.left = "0";
    root.style.transformOrigin = "top left";
    root.style.transform = "none"; // 실제(스케일 전) 콘텐츠 높이를 재기 위해 잠깐 해제

    var scale = window.innerWidth / DESIGN_WIDTH;
    var naturalHeight = root.scrollHeight;

    root.style.transform = "scale(" + scale + ")";

    document.body.style.height = naturalHeight * scale + "px";
  }

  window.addEventListener("resize", fit);
  window.addEventListener("orientationchange", fit);
  document.addEventListener("DOMContentLoaded", fit);
  window.addEventListener("load", fit);
  fit();
})();
