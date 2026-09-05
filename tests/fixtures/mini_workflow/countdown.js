#!/usr/bin/env node
 
const fs = require("fs").promises
 
async function countDown(input, output) {
  let value = parseInt(await fs.readFile(input, "utf-8"))
  console.log(`Old value: ${value}`)
 
  value--
  if (value > 0) {
    console.log(`New value: ${value}`)
    await fs.writeFile(output, "" + value, "utf-8")
  } else {
    console.log("No new value")
  }
}
 
countDown(process.argv[2], process.argv[3]).catch((err) => {
  console.error(err)
  process.exit(1)
})