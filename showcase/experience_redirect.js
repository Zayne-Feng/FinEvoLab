"use strict";
const section = ["#replay", "#evidence"].includes(location.hash)
  ? location.hash
  : "#replay";
location.replace(
  location.hash === "#live"
    ? "https://app.lunelu.com/"
    : "./index.html" + section,
);
