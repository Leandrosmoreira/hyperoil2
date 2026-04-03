# 📊 HyperOil v2 — Estratégia de Dados

## ⚠️ Realidade: Hyperliquid tem apenas ~30 dias de histórico

A Hyperliquid não tem histórico profundo (3-6 meses) para CL/BRENTOIL. Isso é normal em DEX novos.

**Máximo histórico disponível:** ~30 dias

---

## 🎯 Estratégia Recomendada

### **Abordagem: Treinar com 30 dias + Paper Trading Contínuo**

```
SEMANA 1-2: Setup
├── Coletar máximo histórico (30 dias em 15m)
├── Rodar Optuna com 30 dias
├── Gerar best parameters
└── Testar em backtest

SEMANA 3+: Paper Trading (CONTÍNUO)
├── Rodar scripts/run_paper.py
├── Dashboard ao vivo (signal/position/pnl/risk)
├── Coletar dados em tempo real
├── Revalidar estratégia a cada semana
└── Escalar para live quando confiante
```

---

## 💡 Alternativas Consideradas

### ❌ Esperar 6 meses de histórico
- **Problema:** Leva 6 meses
- **Alternativa melhor:** Começar paper trading agora

### ❌ Usar dados de outros exchanges (CME, ICE)
- **Problema:** Diferentes modos de negociação, spreads, horários
- **Alternativa:** Não aplicável (Hyperliquid é perpétuo tokenizado)

### ✅ Usar 30 dias + Paper Trading (RECOMENDADO)
- **Vantagem:** Começa AGORA
- **Validação:** Paper trading valida ao vivo
- **Escalação:** Aumentar capital conforme confiança
- **Histórico:** Acumula dados em tempo real

---

## 🚀 Plano de Ação Imediato

### Passo 1: Coletar 30 dias (10 min)
```bash
cd /opt/hyperoil2
python scripts/collect_data.py --days 30 --interval 15m
# Resultado: 2,881 candles de cada ativo
```

### Passo 2: Otimizar com Optuna (5 min)
```bash
python scripts/run_optuna.py \
  --left data/cl_15m.csv \
  --right data/brent_15m.csv \
  --trials 50 --folds 3
```

### Passo 3: Paper Trading (1+ semana)
```bash
# Roda CONTINUAMENTE
python scripts/run_paper.py

# Coleta dados em tempo real enquanto opera
# Dashboard mostra: signal/position/pnl/risk/system
# Logs em: logs/trades.jsonl
```

### Passo 4: Análise Semanal
```bash
# A cada semana:
# 1. Revisar P&L em papel
# 2. Verificar hit rate (% trades lucrativos)
# 3. Checar drawdown máximo
# 4. Validar stops sendo acionados corretamente
# 5. Aumentar capital gradualmente
```

### Passo 5: Live Trading (Quando Confiante)
```bash
# Após 2-4 semanas de papel com resultados consistentes:
python scripts/run_live.py --confirm

# Começa com capital pequeno (1-2% do disponível)
# Escala gradualmente conforme dados acumulam
```

---

## 📈 Timeline Realista

```
AGORA:        Coletar 30 dias → Optuna → Paper trading inicia
SEMANA 1-2:   Paper trading, coleta de dados em tempo real
SEMANA 3-4:   Análise, retraining com dados acumulados
SEMANA 5+:    Live trading com capital pequeno
MÊS 2:        Escalar conforme histórico aumenta
```

---

## 🎓 Por que Paper Trading é Essencial

Com apenas 30 dias de backtest:
- ❌ Risco de overfitting (apesar de validação WF)
- ❌ Sem validação de edge em condições reais
- ❌ Market structure pode mudar

Paper Trading resolve:
- ✅ Valida ao vivo sem risco
- ✅ Detecta problemas de implementação
- ✅ Coleta dados reais (~2,000 novos candles/semana)
- ✅ Valida hedge ratio (OLS) contra spreads reais
- ✅ Testa kill switch, risk gates, emergencies

---

## 📊 Progressão de Dados

```
Dia 0:   30 dias histórico (2,881 candles 15m)
Semana 1: +672 candles novos (total: 3,553)
Semana 2: +672 candles novos (total: 4,225)
Semana 3: +672 candles novos (total: 4,897)
Semana 4: +672 candles novos (total: 5,569)

Após 4 semanas: 5,569 candles ≈ 39 dias de histórico ✅
Possibilidade: Rodar Optuna novamente com melhor período
```

---

## 🔧 Configuração Recomendada (Start)

Use Fold 1 dos resultados anteriores:

```yaml
# config.yaml
strategy:
  entry_z: 1.3              # Entrada em Z-score 1.3
  exit_z: 0.70              # Saída em Z-score 0.70
  stop_z: 3.0               # Stop loss Z-score 3.0
  z_window: 150             # Rolling window 150 candles
  beta_window: 150          # OLS window 150 candles
  base_notional_usd: 100    # ⚠️ PEQUENO para começar!
  cooldown_bars: 7          # Cooldown 7 candles pós-stop

risk:
  daily_loss_limit_usd: 100         # Stop loss diário pequeno
  max_drawdown_pct: 15.0            # Max drawdown 15%
  max_consecutive_losses: 2         # Pare após 2 stops
  kill_switch_file: "data/KILL_SWITCH"
```

**Importante:** Começar com `base_notional_usd: 100` (pequeno) para:
- Validar lógica sem risco
- Testar hedge ratio
- Acumular mais dados
- Depois escalar para 450 ou mais

---

## 📝 Checklist para Paper Trading

Antes de começar:

```
[ ] 30 dias de dados coletados
[ ] Optuna rodou com sucesso
[ ] config.yaml atualizado com best parameters
[ ] base_notional_usd definido como PEQUENO (100-200)
[ ] daily_loss_limit_usd ajustado para conservador
[ ] .env tem Hyperliquid API key
[ ] data/KILL_SWITCH está pronto (touch data/KILL_SWITCH)
[ ] logs/ diretório existe
[ ] Terminal pode rodar 24h (ou use tmux/screen)
```

Iniciar paper:
```bash
cd /opt/hyperoil2
source .venv/bin/activate
python scripts/run_paper.py
# Ctrl+C para parar (salva estado)
```

---

## 🎯 Métricas para Acompanhar (Paper)

```
Semanal:
  • Total P&L (deve crescer ou ser flat)
  • Hit rate (% de trades lucrativos)
  • Drawdown máximo (não deve exceder daily_loss_limit)
  • Número de stops acionados
  • Tempo médio em posição (bars held)

Mensalmente:
  • Sharpe ratio (paper vs backtest)
  • Profit factor (gross profit / gross loss)
  • Correlation mantém-se estável?
  • Regime changes detectados?
  • Hedge ratio mudou? (verifique OLS window)
```

---

## 🚨 Sinais para Não Começar Live Ainda

```
❌ Hit rate abaixo de 40%
❌ Drawdown excedendo 20%
❌ Hedge ratio muito diferente do backtest
❌ Spreads (CL - BRENTOIL) explodem
❌ Regime changing constantemente
❌ Correlation quebra frequentemente
```

Nesses casos:
- Parar paper trading
- Analisar logs (logs/trades.jsonl)
- Ajustar parâmetros
- Rodar Optuna novamente
- Recomeçar paper

---

## ✅ Sinais para Começar Live

```
✅ Hit rate 50%+ consistentemente
✅ P&L em papel positivo por 2+ semanas
✅ Drawdown controlado (< 10%)
✅ Hedge ratio próximo ao esperado
✅ Regime/correlation comporta conforme esperado
✅ Kill switch testado e funcionando
✅ Risk gates funcionam como esperado
```

Quando todos OK:
```bash
python scripts/run_live.py --confirm
# Começa com base_notional_usd pequeno
# Escala gradualmente: 100 → 200 → 500 → 1000 → etc
```

---

## 📚 Recursos

```
Config:          config.yaml
Backtest data:   data/*.csv
Paper logs:      logs/trades.jsonl
Dashboard:       scripts/run_paper.py (Rich UI)
Kill switch:     data/KILL_SWITCH
```

---

## 🏁 Resumo

| Fase | Duração | O Quê | Risco |
|------|---------|-------|-------|
| **Setup** | 1 dia | Coletar 30d + Optuna | Nenhum |
| **Paper** | 2-4 semanas | Rodar paper.py contínuo | Nenhum (simulado) |
| **Validação** | Semanal | Revisar métricas | Nenhum |
| **Live** | Contínuo | Escalar capital | Pequeno inicialmente |

---

**Conclusão:**
Não é possível coletar 6 meses de histórico. Mas é possível começar AGORA com paper trading e escalar gradualmente conforme confiança cresce.

**Recomendação:** Comece paper trading HOJE.
