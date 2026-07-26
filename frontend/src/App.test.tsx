import { renderToStaticMarkup } from 'react-dom/server'

import { AssistantContent } from './App'

describe('AssistantContent', () => {
  it('renders a model markdown table without exposing markdown syntax', () => {
    const html = renderToStaticMarkup(
      <AssistantContent
        content={`结论：
## 结论

按地区销售额从高到低依次为：**西南 > 华中**。

| 地区 | 销售额 |

|------|-------:|

| 西南 | 292,422 |

| 华中 | 279,797 |

**口径说明**：销售额为有效订单成交总额。

![各地区销售额对比](chart_b07c43a1d42b)

证据：
- 按地区统计销售额 [ev_e2d0d408b317]

口径说明：以上结论仅基于本次已执行查询。`}
      />,
    )

    expect(html).toContain('<table class="answer-table">')
    expect(html).toContain('<strong>西南 &gt; 华中</strong>')
    expect(html).toContain('class="evidence-reference">ev_e2d0d408b317</code>')
    expect(html).toContain('回答边界')
    expect(html).not.toContain('## 结论')
    expect(html).not.toContain('|------|')
    expect(html).not.toContain('chart_b07c43a1d42b')
    expect(html).not.toContain('各地区销售额对比')
  })

  it('keeps a plain conversational answer lightweight', () => {
    const html = renderToStaticMarkup(<AssistantContent content="你好，我是商迹。" />)

    expect(html).toContain('你好，我是商迹。')
    expect(html).not.toContain('answer-report')
  })
})
