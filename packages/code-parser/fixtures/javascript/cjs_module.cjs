const fs = require('fs');
function readIt(p) {
  return fs.readFileSync(p, 'utf8');
}
module.exports = { readIt };
