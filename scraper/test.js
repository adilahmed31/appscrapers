var gplay = require('google-play-scraper');

gplay.search({
    term: "find my phone",
    num: 2
  }).then(console.log, console.log);