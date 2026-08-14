import lunr from "/home/runner/work/zhenxun_docs/zhenxun_docs/node_modules/lunr/lunr.js";
require("/home/runner/work/zhenxun_docs/zhenxun_docs/node_modules/lunr-languages/lunr.stemmer.support.js")(lunr);
require("/home/runner/work/zhenxun_docs/zhenxun_docs/node_modules/@easyops-cn/docusaurus-search-local/dist/client/shared/lunrLanguageZh.js").lunrLanguageZh(lunr);
require("/home/runner/work/zhenxun_docs/zhenxun_docs/node_modules/lunr-languages/lunr.multi.js")(lunr);
export const removeDefaultStopWordFilter = false;
export const language = ["en","zh"];
export const searchIndexUrl = "search-index{dir}.json?_=69fce505";
export const searchResultLimits = 15;