<template>
  <router-view />
</template>

<script setup>
// 使用 Vue Router 来管理页面
</script>

<style>
/* 全局样式重置 */
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

#app {
  /* Sans-first: the UI is set in Space Grotesk / Noto Sans SC; JetBrains Mono
     is opted into per-element via --font-mono (labels, code, metrics). */
  font-family: var(--font-sans, 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizeLegibility;
  color: var(--color-ink, #0A0A0A);
  background-color: var(--color-paper, #ffffff);
}

/* 全局按钮样式 */
button {
  font-family: inherit;
}

/* Printing the primary flow: chrome (nav, rails, controls) is hidden by the
   view-level @media print rules; keep the page itself clean here. */
@media print {
  #app {
    background: #fff;
  }
}
</style>
