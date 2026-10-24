// The toolbar button (or Alt+Shift+F) opens the companion in the browser's side panel.
// Nothing runs on web pages until you press a button in that panel.
function setup() {
  if (chrome.sidePanel?.setPanelBehavior) chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
}
chrome.runtime.onInstalled.addListener(setup);
chrome.runtime.onStartup.addListener(setup);
setup();

// Browsers without a side panel get the same page in a small window next to the tab.
if (!chrome.sidePanel) {
  chrome.action.onClicked.addListener((tab) => {
    chrome.windows.create({ url: `panel.html?tab=${tab.id}`, type: "popup", width: 420, height: 780 });
  });
}
