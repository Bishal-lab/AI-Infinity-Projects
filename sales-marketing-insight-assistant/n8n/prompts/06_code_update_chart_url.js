// Node: "Code in JavaScript1" (Code node)
// Mode: Run Once for All Items | Language: JavaScript
//
// Loop over input items and add a new field called 'updatedChartUrl' to the JSON of each one

function updateChartUrl(url) {
  let updatedUrl = "";
  for (const ch of url) {
    if (ch === "'") {
      updatedUrl += "%27";
    } else if (ch === " ") {
      updatedUrl += "%20";
    } else {
      updatedUrl += ch;
    }
  }
  return "![chart](" + updatedUrl + ")";
}

for (const item of $input.all()) {
  const updatedURL = updateChartUrl(item.json.text);
  item.json.updatedChartUrl = updatedURL;
}

return $input.all();
