<!-- runtime-injected custom properties: statically these look undefined,
     but the framework always sets them. Must NOT be reported. -->
<div style:--stamp-color={stampColor} style:--stamp-rotation={deg}>
  <span style="color: var(--stamp-color); rotate: var(--stamp-rotation);">stamp</span>
</div>
<script>
  const themes = { light: { '--t-bg': '#fff' }, dark: { "--t-bg": '#111' } };
  el.style.setProperty('--t-accent', '#09f');
</script>
<div style="background: var(--t-bg); color: var(--t-accent);">themed</div>
