<script setup lang="ts">
// Hand-drawn 和風 (wafu) line-icon set. Thin, rounded, elegant strokes with
// subtle traditional motifs (seigaiha / asanoha / shippo / fumi). Deliberately
// abstract — no flags or overtly national symbols.
withDefaults(defineProps<{ name: string; size?: number | string }>(), { size: 20 })

// Each entry is the inner markup of a 24x24 viewBox SVG.
const icons: Record<string, string> = {
  // 監視 — scroll/ledger with ink lines
  dashboard:
    '<rect x="3.5" y="4.5" width="17" height="15" rx="2.6"/><path d="M7 9.2h6M7 12h10M7 14.8h4"/>',
  // 重箱 — stacked lacquer boxes (services)
  ports:
    '<rect x="4" y="4.8" width="16" height="6" rx="1.8"/><rect x="4" y="13.2" width="16" height="6" rx="1.8"/><circle cx="7.8" cy="7.8" r="0.7"/><circle cx="7.8" cy="16.2" r="0.7"/>',
  // 七宝 — interlocking circles (models)
  models:
    '<circle cx="12" cy="12" r="7.2"/><path d="M12 4.8a7.2 7.2 0 0 1 0 14.4M4.8 12a7.2 7.2 0 0 1 14.4 0"/><circle cx="12" cy="12" r="2.1"/>',
  // 鍵 — key with circular bow
  keys:
    '<circle cx="8" cy="12" r="3.4"/><path d="M11.4 12H20M17 12v3M20 12v3.4"/>',
  // 文 — folded letter (chat / message)
  chat:
    '<rect x="3.5" y="6" width="17" height="12" rx="2"/><path d="M4 7.5l8 5 8-5"/>',
  // 工房 — asanoha rosette (settings)
  settings:
    '<circle cx="12" cy="12" r="2.6"/><path d="M12 3.4v3.4M12 17.2v3.4M3.4 12h3.4M17.2 12h3.4M6.1 6.1l2.4 2.4M15.5 15.5l2.4 2.4M17.9 6.1l-2.4 2.4M8.5 15.5l-2.4 2.4"/>',
  // 家紋 — seigaiha crest (brand)
  crest:
    '<circle cx="12" cy="12" r="8.6"/><path d="M4.6 13.4a7.4 7.4 0 0 1 14.8 0"/><path d="M7.6 13.4a4.4 4.4 0 0 1 8.8 0"/><circle cx="12" cy="13.4" r="1"/>',
  // 日 — sun disc (light)
  sun:
    '<circle cx="12" cy="12" r="3.8"/><path d="M12 3.2v2.2M12 18.6v2.2M3.2 12h2.2M18.6 12h2.2M5.8 5.8l1.6 1.6M16.6 16.6l1.6 1.6M18.2 5.8l-1.6 1.6M7.4 16.6l-1.6 1.6"/>',
  // 月 — crescent moon (dark)
  moon:
    '<path d="M20 14.4A8 8 0 0 1 9.6 4 7 7 0 1 0 20 14.4Z"/>',
  // 文A — language
  lang:
    '<path d="M4 6.6h6.5M7.2 6.6c0 4-1.4 7.4-3.7 9.6M5.6 11.4c1.5 2.4 3.6 3.8 5 4.4"/><path d="M12.6 18.4l3.4-8.4 3.4 8.4M13.9 15.4h4.2"/>',
  // 退 — door / exit
  logout:
    '<path d="M13.5 4.6H6.2a1.6 1.6 0 0 0-1.6 1.6v11.6a1.6 1.6 0 0 0 1.6 1.6h7.3"/><path d="M12.5 12h7.4M16.6 8.6 20 12l-3.4 3.4"/>',
  // collapse / expand panel
  panelClose: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.2"/><path d="M9.5 4.5v15"/><path d="M16 9l-2.6 3 2.6 3"/>',
  panelOpen: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.2"/><path d="M9.5 4.5v15"/><path d="M13.4 9l2.6 3-2.6 3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  'chevron-right': '<path d="M9 6l6 6-6 6"/>',
  'chevron-down': '<path d="M6 9l6 6 6-6"/>',

  // ---- action / utility icons (筆 brush style) ----
  // 筆 — edit
  edit: '<path d="M5 18.8l1.1-3.6L15.6 5.7a1.6 1.6 0 0 1 2.3 0l.4.4a1.6 1.6 0 0 1 0 2.3L8.7 17.9 5 18.8Z"/><path d="M14.4 7l2.6 2.6"/>',
  // 削除 — trash
  trash: '<path d="M5 7h14M9.5 7V5.6a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1V7M6.6 7l.9 11a1.5 1.5 0 0 0 1.5 1.4h6a1.5 1.5 0 0 0 1.5-1.4l.9-11"/><path d="M10 10.5v6M14 10.5v6"/>',
  // 運行 — play
  play: '<path d="M8 5.6v12.8l10.5-6.4L8 5.6Z"/>',
  // 停止 — stop
  stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="1.6"/>',
  // 写 — copy
  copy: '<rect x="8.5" y="8.5" width="10.5" height="10.5" rx="1.8"/><path d="M15 5.5H6.8A1.3 1.3 0 0 0 5.5 6.8V15"/>',
  // 電源 — power toggle
  power: '<path d="M12 4.5v6.2"/><path d="M7.6 7.2a6 6 0 1 0 8.8 0"/>',
  // 插 — plug / test connection
  plug: '<path d="M9 4.5v4M15 4.5v4"/><path d="M7 8.5h10v1.8a5 5 0 0 1-10 0V8.5Z"/><path d="M12 15.3v4.2"/>',
  // 巻物 — logs (ledger)
  logs: '<rect x="5.5" y="4.5" width="13" height="15" rx="2"/><path d="M8.6 8.6h6.8M8.6 11.6h6.8M8.6 14.6h4"/>',
  // 文書 — file
  file: '<path d="M7 4.5h6l4 4V19a.6.6 0 0 1-.6.6H7a.6.6 0 0 1-.6-.6V5.1A.6.6 0 0 1 7 4.5Z"/><path d="M12.8 4.6V8.6H17"/><path d="M9 12.5h6M9 15.3h4"/>',
  // 取込 — import (up)
  upload: '<path d="M12 15.4V5.4M8.4 9 12 5.4 15.6 9"/><path d="M5.5 14v3.6A1.5 1.5 0 0 0 7 19.1h10a1.5 1.5 0 0 0 1.5-1.5V14"/>',
  // 書出 — export (down)
  download: '<path d="M12 5.4v10M8.4 11.8 12 15.4 15.6 11.8"/><path d="M5.5 14v3.6A1.5 1.5 0 0 0 7 19.1h10a1.5 1.5 0 0 0 1.5-1.5V14"/>',
  // 文庫 — folder open
  folder: '<path d="M4.5 7.6a1.5 1.5 0 0 1 1.5-1.5h2.8l2 2h6.7a1.5 1.5 0 0 1 1.5 1.5v6.3a1.5 1.5 0 0 1-1.5 1.5H6a1.5 1.5 0 0 1-1.5-1.5Z"/>',
  // 保存 — save
  save: '<path d="M6 4.6h9.2l3.2 3.2V18a1.5 1.5 0 0 1-1.5 1.5H6A1.5 1.5 0 0 1 4.5 18V6.1A1.5 1.5 0 0 1 6 4.6Z"/><path d="M8 4.6v4.2h6V4.6M8 19.4v-5h8v5"/>',
  // 送信 — send
  send: '<path d="M19.2 5 4.4 11.4l5.6 2 2 5.6L19.2 5Z"/><path d="M19.2 5 10 14.2"/>',
  // 画像 — image
  image: '<rect x="4.5" y="5.5" width="15" height="13" rx="2"/><circle cx="9" cy="10" r="1.4"/><path d="M5.2 16.2 9.2 12.7l3 2.5 3-3 4.3 4.3"/>',
  // 音声 — microphone
  mic: '<rect x="9.5" y="3.8" width="5" height="9" rx="2.5"/><path d="M6.6 11a5.4 5.4 0 0 0 10.8 0M12 16.4v2.8M9.6 19.2h4.8"/>',
  // 調節 — sliders / params
  sliders: '<path d="M5 8h8M17 8h2M5 16h2M11 16h8"/><circle cx="15" cy="8" r="2"/><circle cx="9" cy="16" r="2"/>',
  // 矢印 — arrow right
  arrow: '<path d="M5 12h13M13 7l5 5-5 5"/>',
  // 注意 — alert
  alert: '<path d="M12 4.6 20 18.6H4L12 4.6Z"/><path d="M12 10v4M12 16.4v.2"/>',
  // 鼓動 — activity
  activity: '<path d="M4 12h3l2.5-6 4 12 2.5-6H20"/>',
  // 盾 — shield (gateway)
  shield: '<path d="M12 4 19 6.4v5c0 4-3 6.8-7 8-4-1.2-7-4-7-8v-5L12 4Z"/><path d="M9.4 11.6l1.9 1.9 3.3-3.5"/>',
  // 絵皿 — palette
  palette: '<path d="M12 4.5a7.5 7.5 0 0 0 0 15c1.2 0 1.8-1 1.4-2.1-.4-1.2.5-2.2 1.8-2.2H17a3 3 0 0 0 3-3A7.3 7.3 0 0 0 12 4.5Z"/><circle cx="8.4" cy="11" r=".9"/><circle cx="12" cy="8.4" r=".9"/><circle cx="15.4" cy="11" r=".9"/>',
  // 錠 — lock
  lock: '<rect x="5.5" y="10.5" width="13" height="9" rx="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/><circle cx="12" cy="14.8" r="1"/>',
  // 網 — network
  network: '<circle cx="12" cy="6" r="2"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="18" r="2"/><path d="M12 8v2.6M11.4 11.4 6.8 16M12.6 11.4 17.2 16"/>',
  // 環 — spinner (enso arc)
  spinner: '<path d="M12 4.5a7.5 7.5 0 1 1-5.3 2.2"/>',
  // 閃き — spark / inspiration (prompt reverse-inference)
  spark: '<path d="M11.5 3.2c.6 3.6 2 5 5.6 5.6-3.6.6-5 2-5.6 5.6-.6-3.6-2-5-5.6-5.6 3.6-.6 5-2 5.6-5.6Z"/><path d="M17.5 13.6c.3 1.6.9 2.2 2.5 2.5-1.6.3-2.2.9-2.5 2.5-.3-1.6-.9-2.2-2.5-2.5 1.6-.3 2.2-.9 2.5-2.5Z"/>',
  // 衆 — users / RBAC
  users: '<circle cx="9" cy="8" r="3.1"/><path d="M3.6 19a5.4 5.4 0 0 1 10.8 0"/><path d="M16 5.4a3 3 0 0 1 0 5.9"/><path d="M15.5 13.4a5.4 5.4 0 0 1 4.9 5.6"/>',
  // 映像 — video
  video: '<rect x="3.2" y="6" width="11.6" height="12" rx="2.2"/><path d="M14.8 10.2 20.5 7.4v9.2l-5.7-2.8Z"/>',
}
</script>

<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" v-html="icons[name] || ''" />
</template>
