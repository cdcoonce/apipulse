### APILayer APIs
| API | Description | Call this API |
|---|---|---|
| [Aggregator Alpha](https://alpha.apilayer-example.com/) | Aggregates things | [![Run In Postman](https://run.pstmn.io/button.svg)](https://app.getpostman.com/run-collection/abc) |
| [Beta Docs](https://beta.apilayer-example.com/) | Documented thing | [Documentation](https://beta.apilayer-example.com/docs) |

### Reordered
| API | AUTH | Description | HTTPS | CORS |
|---|---|---|---|---|
| [Reorder One](https://reorder-one.example.com/) | `apiKey` | Reordered columns | Yes | No |
| [Reorder No](https://reorder-no.example.com/) | No | Free but reordered | Yes | No |

| API | Description | Auth | HTTPS | CORS |
|:---|:---|:---|:---|:---|
| [Ordinary Two](https://ordinary-two.example.com/) | Ordinary columns | `OAuth` | Yes | Yes |

### Headerless
Some prose between tables.
| [Orphan Row](https://orphan.example.com/) | Row with no header | apiKey | Yes | Yes |

### Look-alike
| API | Description | Authentication | HTTPS | CORS |
|---|---|---|---|---|
| [Lookalike](https://lookalike.example.com/) | Not the Auth column | apiKey | Yes | Yes |

### Short rows
| API | Description | HTTPS | CORS | Auth |
|---|---|---|---|---|
| [Short Row](https://short-row.example.com/) | Row stops before Auth | Yes |
| [Full Row](https://full-row.example.com/) | Row reaches Auth | Yes | No | `apiKey` |

### Unpiped
API | auth | Description | HTTPS | CORS
---|---|---|---|---
| [Unpiped One](https://unpiped-one.example.com/) | `apiKey` | Header and separator lack outer pipes | Yes | Yes |
