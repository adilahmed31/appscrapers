var gplay = require('google-play-scraper');

gplay.app({appId: 'com.spotify.tv.android'})
  .then(console.log, console.log);
