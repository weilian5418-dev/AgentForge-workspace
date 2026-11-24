import { readFile, writeFile, mkdir, readdir } from 'node:fs/promises'
import { resolve, join, dirname } from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'

function argumentsMap(values) {
  const result = new Map()
  for (let index = 0; index < values.length; index += 2) {
    const key = values[index]
    const value = values[index + 1]
    if (!key?.startsWith('--') || value === undefined) throw new Error('Expected --name value pairs')
    result.set(key.slice(2), value)
  }
  return result
}

const options = argumentsMap(process.argv.slice(2))
for (const required of ['reference', 'actual', 'out']) {
  if (!options.has(required)) throw new Error(`Missing --${required}`)
}
const projectRequire = createRequire(join(process.cwd(), 'package.json'))
const sharp = projectRequire('sharp')
const pixelmatch = (await import(pathToFileURL(projectRequire.resolve('pixelmatch')).href)).default
const referenceRoot = resolve(options.get('reference'))
const actualRoot = resolve(options.get('actual'))
const outputRoot = resolve(options.get('out'))
if (referenceRoot === outputRoot || actualRoot === outputRoot) throw new Error('Output must differ from input directories')
const limit = options.has('max-ratio') ? Number(options.get('max-ratio')) : null
if (limit !== null && (!Number.isFinite(limit) || limit < 0 || limit > 1)) throw new Error('max-ratio must be between 0 and 1')
const regionLimit = options.has('max-region-ratio') ? Number(options.get('max-region-ratio')) : null
if (regionLimit !== null && (!Number.isFinite(regionLimit) || regionLimit < 0 || regionLimit > 1)) throw new Error('max-region-ratio must be between 0 and 1')
const definitions = JSON.parse(await readFile(resolve(dirname(fileURLToPath(import.meta.url)), '../regions.json'), 'utf8'))
const manifest = JSON.parse(await readFile(resolve(dirname(fileURLToPath(import.meta.url)), '../page-manifest.json'), 'utf8'))
const existingActual = new Set(await readdir(actualRoot))
await mkdir(outputRoot, { recursive: true })
const report = []

for (const page of manifest.pages) {
  const file = page.reference
  if (!existingActual.has(file)) {
    report.push({ page: page.id, status: 'missing-actual' })
    continue
  }
  const reference = await sharp(join(referenceRoot, file)).ensureAlpha().raw().toBuffer({ resolveWithObject: true })
  const actual = await sharp(join(actualRoot, file)).ensureAlpha().raw().toBuffer({ resolveWithObject: true })
  const { width, height } = reference.info
  if (width !== actual.info.width || height !== actual.info.height) {
    report.push({ page: page.id, status: 'size-mismatch', reference: [width, height], actual: [actual.info.width, actual.info.height] })
    continue
  }
  const diff = Buffer.alloc(width * height * 4)
  const changed = pixelmatch(reference.data, actual.data, diff, width, height, { threshold: 0.1, includeAA: false })
  const ratio = changed / (width * height)
  const overlay = Buffer.alloc(width * height * 4)
  for (let offset = 0; offset < overlay.length; offset += 4) {
    for (let channel = 0; channel < 3; channel += 1) overlay[offset + channel] = Math.round((reference.data[offset + channel] + actual.data[offset + channel]) / 2)
    overlay[offset + 3] = 255
  }
  await sharp(diff, { raw: { width, height, channels: 4 } }).png().toFile(join(outputRoot, `${page.id}-diff.png`))
  await sharp(overlay, { raw: { width, height, channels: 4 } }).png().toFile(join(outputRoot, `${page.id}-overlay.png`))
  const regions = []
  for (const [name, rectangle] of Object.entries(definitions[page.id] ?? definitions.default)) {
    const [left, top, regionWidth, regionHeight] = rectangle
    if (left < 0 || top < 0 || left + regionWidth > width || top + regionHeight > height) {
      regions.push({ name, status: 'outside-canvas' })
      continue
    }
    const referenceRegion = await sharp(reference.data, { raw: { width, height, channels: 4 } }).extract({ left, top, width: regionWidth, height: regionHeight }).raw().toBuffer()
    const actualRegion = await sharp(actual.data, { raw: { width, height, channels: 4 } }).extract({ left, top, width: regionWidth, height: regionHeight }).raw().toBuffer()
    const difference = pixelmatch(referenceRegion, actualRegion, null, regionWidth, regionHeight, { threshold: 0.1, includeAA: false })
    regions.push({ name, changedPixels: difference, ratio: difference / (regionWidth * regionHeight) })
  }
  const regionsFail = regions.some(region => region.status === 'outside-canvas' || (regionLimit !== null && region.ratio > regionLimit))
  const status = limit === null && regionLimit === null ? 'diagnostic' : (limit === null || ratio <= limit) && !regionsFail ? 'pass' : 'fail'
  report.push({ page: page.id, status, width, height, changedPixels: changed, ratio, regions })
  console.log(`${page.id}: ${(ratio * 100).toFixed(3)}% ${status}`)
}

await writeFile(join(outputRoot, 'report.json'), JSON.stringify({ referenceRoot, actualRoot, limit, regionLimit, pages: report }, null, 2))
if (report.some(page => ['missing-actual', 'size-mismatch', 'fail'].includes(page.status))) process.exitCode = 1
