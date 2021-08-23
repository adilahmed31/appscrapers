var gplay = require('google-play-scraper');

gplay.search({
    term: "find my phone",
    num: 2
  }).then(console.log, console.log);

var store = require('app-store-scraper');

store.search({
  term: 'panda',
  num: 2,
  page: 3,
  country : 'us',
  lang: 'lang'
})
.then(console.log)
.catch(console.log);