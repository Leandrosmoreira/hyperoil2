# 🕐 HyperOil v2 — Estratégia em Candles 15m

## ✅ Você PODE usar 15m! Aqui estão as opções:

---

## **OPÇÃO 1: Paper Trading Contínuo em 15m (RECOMENDADO)**

### Ideal para: Começar AGORA
```
Vantagem:  • Maior frequência de trades
           • Mais oportunidades por dia
           • Acumula dados rapidamente (~2,000 candles/semana)
           • Backtest com 30 dias é suficiente para validação

Desvantagem: • Menos histórico disponível
             • Mais overhead (mais candles para processar)
             • Mais susceptível a noise em curtos períodos
```

### Como usar:

**Passo 1: Validar com 30 dias de 15m**
```bash
cd /opt/hyperoil2

# Já tem dados: data/cl_15m.csv (2,881 candles)
# Já rodou Optuna: Fold 1 = best params

# Configurar config.yaml com Fold 1:
entry_z: 1.3
exit_z: 0.70
stop_z: 3.0
z_window: 150
beta_window: 150
base_notional_usd: 100
cooldown_bars: 7
```

**Passo 2: Começar Paper em 15m**
```bash
python scripts/run_paper.py

# Roda continuamente em 15m
# Coleta ~2,000 novos candles 15m/semana
# Dashboard ao vivo em Rich UI
```

**Passo 3: Acumular Histórico**
```
Semana 1: 2,881 + 2,000 = 4,881 candles 15m (~34 dias)
Semana 2: 4,881 + 2,000 = 6,881 candles 15m (~48 dias)
Semana 3: 6,881 + 2,000 = 8,881 candles 15m (~62 dias)
Semana 4: 8,881 + 2,000 = 10,881 candles 15m (~76 dias)

Após 4 semanas: ~10,880 candles = ~75 dias de histórico 15m ✅
```

**Passo 4: Rodar Optuna Novamente (após 3-4 semanas)**
```bash
# Após acumular ~60+ dias em tempo real:
python scripts/run_optuna.py \
  --left data/cl_15m.csv \
  --right data/brent_15m.csv \
  --trials 100 --folds 5

# Revalidar parâmetros com mais dados
# Ajustar se necessário
```

**Passo 5: Live Trading em 15m**
```bash
# Após validação bem-sucedida em paper:
python scripts/run_live.py --confirm

# Mesmos parâmetros 15m
# Começar com base_notional_usd pequeno (100)
# Escalar gradualmente
```

---

## **OPÇÃO 2: Backtest em 1h/4h + Live em 15m**

### Ideal para: Validação mais robusta antes de 15m
```
Vantagem:  • Mais histórico com 1h/4h (pode ter 60+ dias)
           • Validação mais robusta (menos noise)
           • Depois opera em 15m (maior frequência)

Desvantagem: • Z-score/correlação calculados em 1h
             • Pode não traduzir direto para 15m
             • Mais complexo
```

### Como testar:

```bash
# Coletar dados 1h (mais histórico)
python scripts/collect_data.py --interval 1h --days 30

# Rodar Optuna em 1h
python scripts/run_optuna.py \
  --left data/cl_1h.csv \
  --right data/brent_1h.csv \
  --trials 50 --folds 5

# Pegar best params de 1h
# Usar em config.yaml (mas para 15m)

# Começar paper/live em 15m com esses params
python scripts/run_paper.py  # ou run_live.py
```

**Risco:** Parâmetros otimizados em 1h podem não funcionar tão bem em 15m (frequências diferentes).

---

## **OPÇÃO 3: Múltiplas Timeframes (Avançado)**

### Usar 1h para decisões + 15m para execução

```yaml
# config.yaml
signal:
  z_window_1h: 100     # Z-score em 1h (decisão)
  z_window_15m: 150    # Z-score em 15m (execução fina)

strategy:
  # Entrada em 1h, execução em 15m
  enter_on_hourly_z: 1.3    # Entrar quando 1h cruza Z=1.3
  fine_tune_15m: true       # Refinar entrada em 15m
```

**Benefício:** Menos sinais falsos (1h menos ruidoso) + mais frequência (15m).

---

## 🎯 **MINHA RECOMENDAÇÃO: OPÇÃO 1**

### Por quê?

1. **Simplicity:** Usar apenas 15m do início ao fim
2. **Consistency:** Mesma timeframe backtest → paper → live
3. **Data Accumulation:** Acumula rapidamente (~2k/semana)
4. **Practical:** Já tem 30 dias de 15m pronto agora
5. **Flexibility:** Ajustar parâmetros a cada semana conforme dados crescem

### Timeline:

```
HOJE:       Rodar paper em 15m com Fold 1 params
SEMANA 1:   Monitor paper, verificar se 15m funciona bem
SEMANA 2:   Acumulou ~5k candles, analisar resultados
SEMANA 3:   Mais validação, ajustes se necessário
SEMANA 4:   Rodar Optuna novamente com ~10k candles 15m
SEMANA 5+:  Live em 15m com parâmetros revalidados
```

---

## ⚙️ **Config para 15m (Paper)**

```yaml
# config.yaml

signal:
  z_window: 150          # 150 × 15m = 2,250 min = ~37.5 horas
  beta_window: 150       # mesmo para OLS

strategy:
  entry_z: 1.3           # Fold 1 best
  exit_z: 0.70
  stop_z: 3.0
  z_window: 150
  beta_window: 150
  base_notional_usd: 100    # PEQUENO para validação
  cooldown_bars: 7          # 7 × 15m = 105 min ≈ 1.75 horas
  max_levels: 3

risk:
  daily_loss_limit_usd: 100      # $100/dia
  max_drawdown_pct: 15.0
  max_consecutive_losses: 2
  kill_switch_file: "data/KILL_SWITCH"

market:
  ws_ping_interval_sec: 30       # Ping a cada 30s (importante em 15m)
```

---

## 📊 **Expectativas em 15m**

Com Z-score em 150 candles 15m (37.5h de janela):

```
Frequência de trades:   ~2-5 trades/dia (vs 0.5-2/dia em 1h)
Tempo em posição:       100-500 min (vs horas em 1h)
Profit/trade:           Menor mas mais frequente
Sharpe ratio:           Similar (idealmente)
Drawdown:               Pode ser mais volatile (mais trades)
```

---

## 🚨 **Cuidados com 15m**

1. **Slippage maior** — Ordens pequenas em 15m podem ser mais voláteis
2. **Fees acumulam** — Mais trades = mais fees (monitore bps)
3. **Noise vs Signal** — 15m tem mais ruído que 1h
4. **Risk Management** — Kill switch mais crítico (mais trades)
5. **Infrastructure** — Precisa de WebSocket stable (15m precisa de latência baixa)

**Mitigation:**
- Começar com `base_notional_usd` pequeno
- `cooldown_bars: 7` para evitar over-trading
- Monitor slippage e fees em paper
- Kill switch pronto
- Rodar em VPS (latência consistente)

---

## ✅ **Checklist para 15m Paper**

```
[ ] config.yaml tem params 15m (Fold 1)
[ ] base_notional_usd = 100 (pequeno)
[ ] z_window = 150 (37.5h janela)
[ ] cooldown_bars = 7 (105min cooldown)
[ ] daily_loss_limit_usd = 100 (conservador)
[ ] data/KILL_SWITCH existe
[ ] logs/ diretório pronto
[ ] .env tem API key
[ ] Dashboard (Rich) funciona
[ ] Rodar 24h em VPS (tmux/screen)
```

---

## 🚀 **Começar AGORA em 15m**

```bash
cd /opt/hyperoil2
source .venv/bin/activate

# 1. Já tem dados 15m (2,881 candles)
# 2. Já rodou Optuna (Fold 1 = best)

# 3. Atualizar config.yaml com Fold 1
# 4. Começar paper em 15m:
python scripts/run_paper.py

# Coleta ~2,000 candles 15m/semana
# Após 4 semanas: ~10,880 candles 15m (~75 dias)
# Depois: revalidar com Optuna, depois live
```

---

## 📈 **Progressão 15m**

```
Dia 0:      2,881 candles 15m (30 dias)
Semana 1:   4,881 candles (34 dias histórico)
Semana 2:   6,881 candles (48 dias)
Semana 3:   8,881 candles (62 dias)
Semana 4:   10,881 candles (76 dias) ← Re-run Optuna aqui

Depois de 4 semanas: Novo Optuna com ~75 dias 15m
Resultado: Parâmetros validados para 15m
```

---

## 🎯 **Resumo**

| Aspecto | 15m Paper (Recomendado) | 1h Backtest + 15m Live | Multi-TF |
|---------|-------------------------|----------------------|----------|
| **Início** | HOJE | 3 dias | HOJE |
| **Complexidade** | Simples ✅ | Média | Complexa |
| **Histórico Inicial** | 30 dias | 60+ dias | 60+ dias |
| **Acumula dados** | Rápido (2k/sem) | Rápido (2k/sem) | Rápido |
| **Validação** | Ao vivo em 15m | 1h, depois 15m | Múltiplos |
| **Recomendado?** | ✅ SIM | Talvez | Avançado |

---

**Conclusão:** Use 15m! Comece paper hoje, acumule dados, revalide em 4 semanas, depois live. 🚀
