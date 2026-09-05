/**
 * Global rules from the prototype's <helmet>, minus the @font-face blocks (fonts come from
 * Google Fonts in index.html). Injected at runtime by the mock route only, so it cannot
 * leak into the real app's styling.
 */
export const PROTO_CSS = `
html,body{margin:0;padding:0;background:#05060f;color:#eef1fb;font-family:"IBM Plex Sans","Noto Sans Myanmar","Noto Sans Thai","Noto Sans JP","Noto Sans KR","Noto Sans SC",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
*{box-sizing:border-box}
a{color:#ffc36b;text-decoration:none}
a:hover{color:#ffdca6}
button{font-family:inherit}
textarea::placeholder,input::placeholder{color:#6f7aa0}
@keyframes lum-spin{to{transform:rotate(360deg)}}
@keyframes lum-bar{0%{transform:translateX(-100%)}100%{transform:translateX(220%)}}
@keyframes lum-pulse{0%,100%{opacity:.4}50%{opacity:1}}
@keyframes lum-rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
@keyframes lum-shim{0%{background-position:-320px 0}100%{background-position:320px 0}}
@keyframes lum-eq{0%,100%{transform:scaleY(.28)}50%{transform:scaleY(1)}}
@keyframes lum-ring{0%{transform:scale(1);opacity:.55}100%{transform:scale(1.7);opacity:0}}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-thumb{background:rgba(150,170,230,.18);border-radius:8px}
::-webkit-scrollbar-track{background:transparent}
`
