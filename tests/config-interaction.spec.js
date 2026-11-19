import { test, expect } from '@playwright/test'
import yaml from 'js-yaml'

test('回调订阅开关默认关闭，按机器人保存并在重新加载后保持', async ({ context, page }) => {
  let bot = yaml.dump({ bots: [{ appid: '10001' }, { appid: '10002' }] })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => {
    if (message.type() === 'warning' && message.text().includes('Failed to resolve component')) errors.push(message.text())
  })
  await context.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    let result = { success: true }
    if (path === '/api/bots') result.bots = []
    if (path === '/api/web-pages') result.pages = []
    if (path === '/api/config') Object.assign(result, { bot, settings: '{}', templates: '{}' })
    if (path === '/api/config/save') {
      const body = route.request().postDataJSON()
      expect(body.file).toBe('bot')
      bot = body.content
    }
    await route.fulfill({ json: result })
  })
  await page.goto('/web/config')
  const toggle = page.getByRole('switch', { name: '回调事件需显式订阅', exact: true })
  const errorReply = page.getByRole('switch', { name: '消息发送失败时回复模板', exact: true })
  const bots = page.getByRole('combobox', { name: '选择机器人' })
  const save = page.getByRole('button', { name: '保存配置', exact: true })
  await expect(toggle).not.toBeChecked()
  await expect(errorReply).toBeChecked()
  await toggle.check()
  await save.click()
  await expect(save).toBeDisabled()
  expect(yaml.load(bot).bots[0].message.interaction_explicit_only).toBe(true)
  expect(yaml.load(bot).bots[1].message?.interaction_explicit_only).toBeUndefined()

  await page.reload()
  await expect(toggle).toBeChecked()
  await bots.selectOption('1')
  await expect(toggle).not.toBeChecked()
  await bots.selectOption('0')
  await toggle.uncheck()
  await errorReply.uncheck()
  await save.click()
  await expect(save).toBeDisabled()
  await page.reload()
  await expect(toggle).not.toBeChecked()
  await expect(errorReply).not.toBeChecked()

  await page.getByRole('button', { name: '添加', exact: true }).click()
  await expect(toggle).not.toBeChecked()
  await expect(errorReply).toBeChecked()
  await page.getByLabel('AppID', { exact: true }).fill('10003')
  await save.click()
  await expect(save).toBeDisabled()
  expect(yaml.load(bot).bots[2].message.interaction_explicit_only).toBe(false)
  expect(yaml.load(bot).bots[2].message.send_error_response).toBe(true)
  expect(errors).toEqual([])
})
