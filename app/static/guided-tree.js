// The guided questions on the welcome screen, under the question box: a small decision tree. Edit it here.
//
// A question has a label and options (at most three levels from the top, counting the top). An end point has a
// label and one of:
//   guide: a guide's slug (guides/index.json), with anchor: the id of one of its headings ("h-" and the heading's
//          words in lower case joined by "-"), to open the guide at that heading; leave anchor out to open it at the top
//   search: words to look for in the library's titles (the Library page's "Find a title")
//   ask: words put in the question box, for the person to finish and send
// call: true on a question shows the current country's emergency number first, before its options, as "Call … now."
// Labels only point somewhere: the advice is the guide's own. tests/interface_guided_check.js walks every path and
// checks each end point is a guide (and heading) that exists, a search, or a question.
(function (root) {
  "use strict";
  const TREE = {
    options: [
      {
        label: "Is someone hurt or in danger right now?", call: true, options: [
          { label: "Someone is injured", options: [
            { label: "Bleeding heavily", guide: "bleeding-burns-and-heat-stroke", anchor: "h-severe-bleeding" },
            { label: "A burn", guide: "bleeding-burns-and-heat-stroke", anchor: "h-burns" },
            { label: "Too hot, confused or fainting", guide: "bleeding-burns-and-heat-stroke", anchor: "h-heat-exhaustion-and-heat-stroke" },
          ] },
          { label: "Someone has collapsed or is choking", options: [
            { label: "An adult not breathing normally", guide: "cpr-and-choking", anchor: "h-cpr-for-an-adult" },
            { label: "A child or baby not breathing normally", guide: "cpr-and-choking", anchor: "h-cpr-for-a-child-or-a-baby" },
            { label: "An adult or child choking", guide: "cpr-and-choking", anchor: "h-choking-adult-or-child" },
            { label: "A baby choking", guide: "cpr-and-choking", anchor: "h-choking-baby" },
          ] },
          { label: "Fire, smoke or fumes", options: [
            { label: "A fire in the building", guide: "fire-at-home-or-in-your-building", anchor: "h-if-you-find-a-fire" },
            { label: "Trapped by fire or smoke", guide: "fire-at-home-or-in-your-building", anchor: "h-if-you-are-trapped" },
            { label: "Fumes from a generator, heater or engine", guide: "power-lines-and-carbon-monoxide", anchor: "h-carbon-monoxide-poisoning-what-to-do-at-once" },
            { label: "A power line down", guide: "power-lines-and-carbon-monoxide" },
          ] },
          { label: "Something else", ask: "Someone is hurt or in danger: " },
        ],
      },
      {
        label: "Has something just happened (fire, flood, power cut, alert)?", options: [
          { label: "A siren or an alert", call: true, options: [
            { label: "An air-raid siren", guide: "war-and-air-raid-alerts", anchor: "h-when-the-warning-sounds" },
            { label: "A nuclear warning", guide: "nuclear-incident" },
            { label: "A chemical smell, smoke or gas outside", guide: "chemical-spill-or-toxic-smoke", anchor: "h-do-this-first" },
          ] },
          { label: "A fire in the building", call: true, options: [
            { label: "Getting everyone out", guide: "fire-at-home-or-in-your-building", anchor: "h-if-you-find-a-fire" },
            { label: "Through smoke", guide: "fire-at-home-or-in-your-building", anchor: "h-getting-out-through-smoke" },
            { label: "Trapped by fire or smoke", guide: "fire-at-home-or-in-your-building", anchor: "h-if-you-are-trapped" },
          ] },
          { label: "A flood or a storm", call: true, options: [
            { label: "A flood warning", guide: "flooding-and-storms", anchor: "h-when-a-flood-warning-comes" },
            { label: "Trapped by flood water", guide: "flooding-and-storms", anchor: "h-if-you-are-trapped" },
            { label: "Thunder and lightning", guide: "flooding-and-storms", anchor: "h-thunderstorms-and-lightning" },
          ] },
          { label: "Power, water or phone is out", options: [
            { label: "A power cut", guide: "no-electricity", anchor: "h-do-this-first" },
            { label: "No running water", guide: "no-running-water", anchor: "h-do-this-first" },
            { label: "No phone or internet", guide: "no-internet-or-phone-network" },
            { label: "Cold at home without power", guide: "staying-warm-without-power" },
          ] },
          { label: "We have to leave home", guide: "leaving-home-in-a-hurry", anchor: "h-before-you-leave" },
          { label: "An illness spreading at home", guide: "disease-outbreak-at-home", anchor: "h-stop-the-spread" },
          { label: "Something else", ask: "Something has just happened: " },
        ],
      },
      {
        label: "Are you preparing ahead?", options: [
          { label: "An emergency kit", options: [
            { label: "A go-kit and a stay-at-home kit", guide: "prepare-ahead", anchor: "h-the-emergency-kit" },
            { label: "A go-bag to grab and leave", guide: "leaving-home-in-a-hurry", anchor: "h-the-go-bag" },
          ] },
          { label: "Water and food to store", options: [
            { label: "Water", guide: "prepare-ahead", anchor: "h-water" },
            { label: "Food to store", guide: "prepare-ahead", anchor: "h-food" },
            { label: "Look up food storage in the library", search: "Food storage" },
          ] },
          { label: "A shelter", options: [
            { label: "Find a good shelter", guide: "finding-a-good-shelter" },
            { label: "Set up a shelter room at home", guide: "preparing-a-home-shelter" },
          ] },
          { label: "A family plan", guide: "prepare-ahead", anchor: "h-a-family-plan" },
          { label: "Iodine tablets", guide: "iodine-tablets-by-age", anchor: "h-dose-by-age" },
          { label: "Something else", ask: "How do I prepare for " },
        ],
      },
    ],
  };
  if (typeof module !== "undefined" && module.exports) module.exports = TREE;
  root.SLGuidedTree = TREE;
})(typeof window !== "undefined" ? window : globalThis);
