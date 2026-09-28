/**
 * 피그마 디자인 그대로(402x874 고정 픽셀) 만들어진 /app 페이지들을 실제 폰 화면 크기에
 * 맞춰 통째로 축소/확대해서 스크롤 없이 한 화면에 딱 맞게 보여준다.
 * 각 페이지 내부 CSS(절대좌표 등)는 전혀 건드리지 않고, body의 첫 번째 자식(디자인 루트
 * 래퍼 div)을 그대로 스케일링하는 방식이라 모든 /app 페이지에 공통으로 적용 가능하다.
 */
(function () {
  var DESIGN_WIDTH = 402;
  var DESIGN_HEIGHT = 874;

  function fit() {
    var root = document.body.firstElementChild;
    if (!root) return;

    root.style.width = DESIGN_WIDTH + "px";
    root.style.minWidth = DESIGN_WIDTH + "px";
    root.style.height = DESIGN_HEIGHT + "px";
    root.style.minHeight = DESIGN_HEIGHT + "px";
    root.style.maxHeight = DESIGN_HEIGHT + "px";
    root.style.position = "absolute";
    root.style.transformOrigin = "top left";

    var scale = Math.min(window.innerWidth / DESIGN_WIDTH, window.innerHeight / DESIGN_HEIGHT);
    root.style.transform = "scale(" + scale + ")";

    var offsetX = Math.max(0, (window.innerWidth - DESIGN_WIDTH * scale) / 2);
    var offsetY = Math.max(0, (window.innerHeight - DESIGN_HEIGHT * scale) / 2);
    root.style.left = offsetX + "px";
    root.style.top = offsetY + "px";
  }

  window.addEventListener("resize", fit);
  window.addEventListener("orientationchange", fit);
  document.addEventListener("DOMContentLoaded", fit);
  window.addEventListener("load", fit);
  fit();
})();
