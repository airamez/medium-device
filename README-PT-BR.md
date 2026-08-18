# Dispositivo de Entrada de Caracteres com Agulha Circular

Este projeto é uma forma física de digitar letras. Uma agulha gira livremente sobre um disco circular marcado com **A–Z** e **0–9**. Você aponta a agulha para um caractere, mantém ela parada, e esse caractere aparece no computador.

Nada no disco é um interruptor. Um ímã no eixo gira a uma pequena distância acima de um encoder magnético AS5600. Um Arduino Nano lê o ângulo e envia pelo USB. Um programa em Python no computador transforma esse ângulo em letras, imprime no console e grava um arquivo de log.

Monte nesta ordem: entenda o projeto, instale o software que o computador precisa, comprove a eletrônica na protoboard (sem cola), depois monte o disco de madeira, e então calibre e digite. Não cole nem monte as partes mecânicas até conseguir ver o ângulo mudar no computador.

---

## Sumário

1. [O que você está construindo](#o-que-você-está-construindo)
2. [Como funciona](#como-funciona)
3. [Os dois programas](#os-dois-programas)
4. [Peças](#peças)
5. [Instalação](#instalação)
6. [Montagem e execução](#montagem-e-execução)
7. [Mecânica](#mecânica)
8. [Digitar letras](#digitar-letras)
9. [Referência do capture.py](#referência-do-capturepy)
10. [Folha de consulta do Arduino](#folha-de-consulta-do-arduino)
11. [Pronto quando](#pronto-quando)

---

## O que você está construindo

```
Você move a agulha (M03)
        |
        v
O eixo (M02) gira em um rolamento (M01)
        |
        v
O ímã (E03) na ponta do eixo gira
        |
        v
O AS5600 (E01) mede o ângulo 0–360°  (sem contato)
        |
        v
O Nano (E02) imprime  a=123.4  pelo USB (E06)
        |
        v
Python no PC  -->  mapeia o ângulo para uma letra  -->  console + arquivo de log
```

| Gira como uma peça só | Permanece fixo |
|-----------------------|----------------|
| Agulha (M03), eixo (M02), ímã (E03) | Anel externo do rolamento (M01), base de madeira (M04), sensor, Nano, PC |

O firmware no Nano é deliberadamente simples: ele só transmite ângulos.  
O programa Python no computador faz o resto: um mapa de 36 caracteres, uma verificação rápida de A / J / S / 1 em cada sessão, detecção de estabilidade e registro em log.

---

## Como funciona

```
  VISTA DE CIMA                    PILHA LATERAL (centro)

      7  A  D                         M03  ========●========► agulha
    4         G                            |
  1      ●------► M                   M02  |  eixo
    Y         J                            |
      V  S  P                         M01  (====)  rolamento na base M04
                                           |
                                      E03  [N|S]   ímã na PONTA do eixo
                                           |  folga de ar 1–3 mm
                                      E01  [AS5600]
                                           |
                                      E04  fios
                                      E02  [Nano] ---- E06 USB ---- PC
```

![Part map](docs/device-diagram.png)

Os códigos dos itens coincidem com a [lista de peças](#peças): **E** é eletrônica, **M** é mecânica.

O ímã precisa ser um disco **diametral** (os polos ficam em lados opostos da face, não nas duas faces planas). Ele fica na **ponta de baixo** do eixo, 1–3 mm acima do chip preto do AS5600. O eixo passa só pelo rolamento, não pelo ímã.

Os discos impressos (imprima em 100%, sem ajustar à página) estão em `docs/base-templates/`. Os tamanhos vão de 6" a 10". **A** fica no norte. As letras seguem no sentido horário, A–Z e depois 0–9. As letras ficam **fora** do círculo de corte. 6" e 7" cabem em papel carta (letter); 8", 9" e 10" cabem em tabloide (11×17).

Cada tamanho tem vários layouts (os nomes abaixo são os arquivos de 6"):

| Arquivo | Layout |
|------|--------|
| `dial-6in.pdf` | Raios do centro até cada caractere |
| `dial-6in-nolines.pdf` | Só as letras, sem raios |
| `dial-6in-big.pdf` | Letras maiores, com raios |
| `dial-6in-big-nolines.pdf` | Letras maiores, sem raios |
| `dial-6in-box.pdf` | Um quadradinho **logo antes** de cada caractere — aponte a agulha para dentro da caixa |
| `dial-6in-big-box.pdf` | Letras maiores com as caixas de mira |

Os arquivos `-box` são os mais fáceis de apontar: a caixa fica na borda, alinhada com a letra, para você ver quando o ponteiro está nesse caractere.

### Organização do repositório

```
medium-device/
  README.md
  docs/                diagramas, fotos
  docs/base-templates/ discos para imprimir (6–10 in, vários layouts)
  firmware/            sketches Arduino (abra estes somente no Arduino IDE)
  host/                programa Python de captura
  logs/                criado em tempo de execução
```

---

## Os dois programas

Você usa **duas ferramentas diferentes**. Elas não são intercambiáveis.

| | O que faz | Qual aplicativo | Arquivo |
|---|-----------|-----------------|---------|
| 1 | Coloca o código **no Nano** para a placa ler o ímã | **Arduino IDE** | `firmware/needle_angle_stream/needle_angle_stream.ino` |
| 2 | Mostra esses números **no computador** e depois transforma em letras | **Terminal** → Python | `host/capture.py` |

Algumas coisas fáceis de confundir:

- O arquivo `.ino` **não** é Python. Não execute com `python`.
- Abra somente no Arduino IDE, em **File → Open**.
- O `capture.py` não tem nada para ler até o firmware ser gravado com sucesso.
- Um LED vermelho ou piscando no Nano significa que a placa tem **energia**. Muitas vezes isso é o sketch de fábrica (pisca-pisca). Não é o programa deste projeto até você clicar em Upload e a gravação terminar.

O restante deste documento assume que esses dois papéis continuam separados: o Arduino IDE fala com o Nano; o Python fala com a porta serial que o Nano cria.

---

## Peças

### Itens

| Código | Item | O que faz | Link |
|------|------|--------------|------|
| **E01** | Módulo AS5600 | Mede o ângulo do ímã | [HiLetgo](https://www.amazon.com/HiLetgo-Magnetic-Encoder-Measurement-Precision/dp/B09KGWC1PT) |
| **E02** | Arduino Nano (ATmega328P + CH340) | Lê o E01 e envia serial USB | [Nano V3.0, Nano Board ATmega328P...](https://www.amazon.com/dp/B07G99NNXL?ref=ppx_yo2ov_dt_b_fed_asin_title) |
| **E03** | Disco ímã diametral | Gira com o eixo para o E01 detectar | [Eliveshown](https://www.amazon.com/Eliveshown-Diametrically-Neodymium-6-35x6-35-diametrical/dp/B0D2C9VNVR) — pule se já vier com o E01 |
| **E04** | Jumpers Dupont | Ligam os pinos do E01 aos pinos do E02 | [EDGELEC](https://www.amazon.com/EDGELEC-Optional-Breadboard-Assorted-Multicolored/dp/B07GCZ52WF) |
| **E05** | Protoboard de meia | Fiação temporária, sem solda | [ELEGOO](https://www.amazon.com/ELEGOO-tie-points-breadboard-Arduino-Jumper/dp/B01EV640I6) |
| **E06** | Cabo USB **de dados** | Energia e serial (Mini-B ou USB-C) | Muitas vezes vem no kit do Nano; precisa transmitir dados, não ser só de carga |
| **M01** | Rolamentos de esferas | Rotação da haste | [Amazon](https://a.co/d/0bbrJnqB) |
| **M02** | Haste de aço inoxidável 3 mm | Eixo | [Sutemribor](https://www.amazon.com/Sutemribor-100mm-Straight-Helicopter-Airplane/dp/B076XY82K3) |
| **M03** | Palitos de balsa | Agulha leve | [Amazon](https://www.amazon.com/Perfect-Modeling-Hobbies-Architecture-Mockups/dp/B0BYXN3443) |
| **M04** | Círculo de madeira ~10" | Disco / base | [Woodpeckers](https://www.amazon.com/Wooden-Plaques-Package-Unfinished-Woodpeckers/dp/B07VVFPFZR) |
| **M05a** | Supercola | Fixa o ímã e a agulha no eixo | [Loctite](https://www.amazon.com/Loctite-Super-Glue-Liquid-Professional/dp/B0CLQCKVDX) |
| **M05b** | Parafusos pequenos + porcas para contrapeso da base | |

Kits Nano da ELEGOO **sem cabo** precisam de um cabo **USB Mini-B de dados** separado. Alguns Nanos vêm com headers soltos; esses headers precisam ser **soldados** antes da placa encaixar na protoboard.

### Como cada item funciona

**Eletrônica**

| Código | O que é | Como funciona | Onde |
|------|------------|--------------|--------|
| **E01** | PCB AS5600 | O chip Hall lê o campo do ímã e informa um ângulo 0–360° | Sob o centro de M04, chip virado para o ímã |
| **E02** | Arduino Nano | Conversa I2C com o E01; imprime `a=123.4` pelo USB | Protoboard nos testes, ou sob a base depois |
| **E03** | Disco diametral maciço | O campo gira com o eixo | Colado na **ponta de baixo** de M02, 1–3 mm acima do E01 |
| **E04** | Fios Dupont | Levam 5V, GND, SDA, SCL e DIR→GND | Entre E01 e E02 |
| **E05** | Protoboard | Contatos sem solda | Bancada, fase da eletrônica |
| **E06** | Cabo USB | Energia e dados | Do E02 ao computador |

O E03 é um **disco maciço** (em geral sem furo). Cole na **ponta** do eixo. O eixo passa **somente pelo rolamento (M01)**, não pelo ímã.

**Mecânica**

| Código | O que é | Como funciona | Onde |
|------|------------|--------------|--------|
| **M01** | Rolamento de esferas com ID 3 mm | Anel externo fixo; o eixo gira dentro | Furo central de M04 |
| **M02** | Haste de 3 mm, cerca de 25–40 mm | Une a agulha e o ímã | Através de M01 |
| **M03** | Ponteiro de balsa | O que você vê se mover | Topo de M02 |
| **M04** | Círculo de madeira | Anel das letras e estrutura | Mesa |
| **M05a** | Cola CA | Fixa E03 e M03 em M02 | Pontas do eixo |
| **M05b** | Parafuso e porca | Equilibra a agulha | Extremidade curta de M03 |

### Ferramentas

**Obrigatórias:** um computador de mesa ou notebook, um cabo USB de dados, tesoura ou cortador, uma régua, um lápis.

**Opcionais:** uma chave de fenda pequena, um transferidor ou um gabarito impresso de 10°, uma furadeira (cerca de 8 mm para o rolamento), um multímetro (somente se a fiação falhar). Você não precisa de voltímetro se os ângulos forem impressos. No Windows também pode ser necessário um driver USB-serial CH340; isso está em [Instalação](#instalação).

---

## Instalação

Antes de ligar qualquer fio, instale os dois programas dos quais este projeto depende:

1. **Arduino IDE** — usado depois para compilar o sketch e gravá-lo no Nano.
2. **Python 3** com a biblioteca **pyserial** — usado depois para ler o Nano e digitar letras.

Letras faladas (`--sound`) são opcionais. Se quiser, instale também um sintetizador de voz. Tudo abaixo funciona offline e não precisa de conta.

Este projeto roda em um **PC Windows, um Mac ou um computador Linux**. Não roda em iPhone nem iPad.

Conecte o Nano só depois que o software estiver instalado, para confirmar que o computador enxerga a porta USB.

### Windows

1. Instale o [Arduino IDE](https://www.arduino.cc/en/software) (2.x serve). Use o instalador do site do Arduino.
2. Instale o [Python 3](https://www.python.org/downloads/). Na primeira tela do instalador, marque **Add python.exe to PATH**.
3. Abra o **Prompt de Comando** ou o **PowerShell** neste repositório e instale o pyserial:

   ```bat
   python -m pip install pyserial
   ```

   Um ambiente virtual é opcional, mas deixa tudo mais organizado:

   ```bat
   python -m venv .venv
   .venv\Scripts\activate
   python -m pip install -r host\requirements.txt
   ```

4. Opcional, para letras faladas: `python -m pip install pyttsx3`. No Windows isso usa as vozes de fala já instaladas.
5. Conecte o Nano com um cabo **de dados**. Um LED de energia deve acender.
6. No Arduino IDE, abra **Tools → Port**. Você deve ver uma porta `COMx` (por exemplo `COM3`).

Se nenhuma porta COM aparecer: instale um driver USB-serial **CH340**, desconecte e reconecte a placa, e tente outro cabo. Cabos só de carga alimentam o LED e mesmo assim não criam porta.

### macOS

1. Instale o [Arduino IDE](https://www.arduino.cc/en/software) (2.x serve).
2. Instale o Python 3 se o Mac ainda não tiver. [python.org](https://www.python.org/downloads/) ou Homebrew (`brew install python`) funcionam.
3. Abra o **Terminal** neste repositório e instale o pyserial:

   ```bash
   python3 -m pip install pyserial
   ```

   Ou use um ambiente virtual:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r host/requirements.txt
   ```

4. Opcional, para letras faladas: `pip install pyttsx3`.
5. Conecte o Nano com um cabo **de dados**. Um LED de energia deve acender.
6. No Arduino IDE, abra **Tools → Port**. Você deve ver algo como `/dev/cu.usbserial-…` ou `/dev/cu.wchusbserial-…`.

Se nenhuma porta aparecer, instale um driver **CH340** para macOS, depois desconecte e reconecte. Como no Windows, um cabo só de carga não funciona.

### Linux

1. Instale o [Arduino IDE](https://www.arduino.cc/en/software) (2.x serve). Muitas distribuições também o empacotam; qualquer origem serve, desde que você consiga abrir o aplicativo e escolher **Arduino Nano**.
2. Instale o Python 3 e o pyserial. Prefira o pacote da distribuição, ou um ambiente virtual. Não brigue com o instalador do sistema com um `pip install` solto se ele recusar (PEP 668).

   **Arch / Manjaro**

   ```bash
   sudo pacman -S python-pyserial
   ```

   **Debian / Ubuntu**

   ```bash
   sudo apt install python3-serial
   ```

   **Fedora**

   ```bash
   sudo dnf install python3-pyserial
   ```

   **Qualquer distribuição, isolado neste repositório**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r host/requirements.txt
   ```

   Se usar o ambiente virtual, ative-o antes de cada comando `python host/capture.py`: `source .venv/bin/activate`.

3. Opcional, para letras faladas:

   ```bash
   # Arch / Manjaro
   sudo pacman -S espeak-ng

   # Debian / Ubuntu
   sudo apt install espeak-ng

   # Fedora
   sudo dnf install espeak-ng
   ```

   `espeak` ou `speech-dispatcher` (`spd-say`) também funcionam se já estiverem instalados. `pip install pyttsx3` é um plano B; no Linux essa biblioteca ainda precisa do **espeak-ng**. Os alto-falantes ou fones já precisam reproduzir som.

4. Conecte o Nano com um cabo **de dados**. Um LED de energia deve acender.
5. No Arduino IDE, abra **Tools → Port**. Você deve ver `/dev/ttyUSB0` ou `/dev/ttyACM0`.

No Linux a porta muitas vezes aparece e depois recusa abrir até o usuário estar no grupo serial. Isso não é cabo quebrado.

- No Arch e no Manjaro o grupo é **`uucp`**.
- No Debian, Ubuntu e Fedora o grupo é **`dialout`**.

```bash
ls -l /dev/ttyUSB0          # veja qual grupo é dono da porta
sudo usermod -aG uucp "$USER"      # Arch / Manjaro
# sudo usermod -aG dialout "$USER" # Debian / Ubuntu / Fedora
```

Depois **saia da sessão do desktop por completo e entre de novo**. Abrir um terminal novo não basta. Confira com `groups` — você precisa ver `uucp` ou `dialout`. Na mesma sessão, sem sair: `newgrp uucp` (ou `newgrp dialout`), e então inicie o Arduino IDE **daquele** terminal.

### iOS (iPhone e iPad)

Este projeto precisa de um computador de mesa ou notebook. O Arduino IDE, uma conexão serial USB com o Nano e o programa Python de captura não rodam no iOS nem no iPadOS. Use um PC Windows, um Mac ou uma máquina Linux como host.

---

## Montagem e execução

Faça a eletrônica funcionar antes de qualquer cola. O objetivo desta seção é simples: gire o ímã com a mão e veja linhas `a=…` mudando no computador.

### O que você precisa nesta seção

- Arduino IDE, já instalado
- O Nano, o AS5600, jumpers e a protoboard
- Um cabo USB **de dados**
- O ímã diametral, na mão (sem colar)
- Python e pyserial, já instalados, se quiser acompanhar o fluxo pelo terminal

### Como a protoboard funciona

**Não** use as faixas longas de **+** e **−** a menos que um passo posterior diga para usar. Esses trilhos **não** são 5V nem GND até você colocar jumpers extras. Este projeto não os usa.

As **fileiras numeradas** do meio é que importam. Em uma fileira, de **um** lado da fenda central, os cinco furos são o mesmo fio:

```
     +  -                     -  +     <-- ignore estes trilhos
     +  -                     -  +

        a  b  c  d  e     f  g  h  i  j
     1  o  o  o  o  o  |  o  o  o  o  o
     2  o  o  o  o  o  |  o  o  o  o  o
     3  o  o  o  o  o  |  o  o  o  o  o   <-- fileira 3 esquerda NÃO é fileira 3 direita
        ============= fenda ============
```

Exemplo: o pino **5V** do Nano fica na **fileira 20**, lado direito (f–j).  
Encaixe o jumper **VCC** do AS5600 em **outro furo da fileira 20, mesmo lado** (f–j). Isso *é* ligar em 5V.

```
  Errado:  jumper VCC em um furo do trilho +
  Errado:  jumper VCC em uma fileira numerada qualquer
  Certo:   jumper VCC na MESMA fileira numerada do pino 5V do Nano,
           MESMO lado da fenda
```

Coloque o Nano **atravessando a fenda** (USB em uma extremidade). Os pinos da esquerda usam as colunas a–e; os da direita usam f–j.

Ache o **nome impresso** no Nano (5V, GND, A0, A4, A5). Veja em qual **número** esse pino está. Use esse número.

```
   Protoboard E05 (vista de cima)

   [  +  -  . . . . . . . . . .  -  +  ]   trilhos de energia
   [  +  -  . . . . . . . . . .  -  +  ]

        .  .  .  .  .  .  .  .  .  .         uma fileira = um nó elétrico
        ================================     fenda central
        .  .  .  .  .  .  .  .  .  .

   Coloque o Nano ATRAVESSANDO a fenda
   para os pinos esquerdos e direitos ficarem em metades opostas.
```

```
              USB (E06) para o computador
                    |
              +-----+-----+
              |           |
              |   NANO    |   <-- atravesse a fenda
              |   E02     |
              +-----------+
   pinos esquerdos nos furos esquerdos     pinos direitos nos furos direitos
```

O Nano precisa ter headers de pino para sentar na protoboard. Se o kit for de “headers soltos”, solde-os primeiro (ou use um Nano já soldado).

### Identifique os pinos

Olhe a serigrafia do Nano. Você precisa destes:

```
   Nano típico (USB em cima)

        [USB]
   D13               VIN
   ...               GND     <-- use este GND (preto + DIR roxo)
   ...               5V      <-- energia para o E01 (ou 3.3V se o módulo for só 3.3V)
   ...               A7
   ...               A6
   ...               A5 SCL  <-- clock para o E01
   ...               A4 SDA  <-- dados para o E01
   ...               A3
```

Os nomes dos pinos estão impressos na placa. **A4** e **A5** ficam um ao lado do outro no lado analógico.

No módulo AS5600, ache as legendas e use estes cinco:

```
   Módulo E01 (exemplo)

   [  SCL ]---- para o Nano A5
   [  SDA ]---- para o Nano A4
   [  GND ]---- para o Nano GND
   [  VCC ]---- para o Nano 5V   (3.3V se a placa disser só 3.3V)
   [  DIR ]---- para o Nano GND  (ou um jumper curto até o pino GND do módulo)

   Deixe abertos: OUT, PGO, GPO (se existirem)

   O CI preto pequeno no meio é o sensor.
   O ímã fica sobre ESSE chip, não sobre a PCB inteira.
```

### Fotos da fiação

- Arduino Nano:
  
  ![Arduino nano](docs/Arduino.webp)

- AS5600:
  
  ![AS5600](docs/AS5600.jpg)

- Mapa de pinos do Nano (neste projeto):

  ![AS5600 to Nano jumpers](docs/nano-as5600-jumpers.png)

- Os mesmos cinco fios, placas como ficam na bancada:

  ![Arduino Nano and AS5600 wiring](docs/nano-as5600-breadboard.png)

- Vermelho: **VCC → 5V**
- Preto: **GND → GND**
- Azul: **SDA → A4**
- Amarelo: **SCL → A5**
- Roxo: **DIR → GND** (a mesma fileira GND do Nano do fio preto, ou um jumper curto de **DIR** para **GND** no módulo)

**Não** deixe **DIR** flutuando. Um pino DIR flutuante faz o ângulo de 12 bits pular e parecer ruidoso. Ligar em GND trava a contagem no sentido horário (visto de cima do chip). Ligar em VCC inverteria a contagem; este projeto usa GND.

Se quiser as notas do datasheet por trás dessa regra:

- DIR precisa ser GND ou VCC, nunca flutuante: https://esphome.io/components/sensor/as5600/
- Leituras oscilando → ligue DIR em GND: https://curiousscientist.tech/blog/as5600-magnetic-position-encoder
- Pino de direção: https://github.com/RobTillaart/AS5600#dir-pin

### Ligue os cinco cabos

Desconecte o USB primeiro.

```
AS5600 (E01)          Nano (E02)
VCC  ---------------  5V     (use 3.3V só se o E01 disser só 3.3V)
GND  ---------------  GND
SDA  ---------------  A4
SCL  ---------------  A5
DIR  ---------------  GND    (o mesmo GND do fio preto — não deixe DIR aberto)
Nano USB  ----------  computador
```

| Pino E01 | Ligue no pino E02 | Cor sugerida | Função |
|---------|-----------------|-----------------|------|
| **VCC** | **5V** | Vermelho | Energia (use **3.3V** se o E01 estiver marcado só 3.3V) |
| **GND** | **GND** | Preto | Terra (obrigatório) |
| **SDA** | **A4** | Azul / branco | Dados I2C |
| **SCL** | **A5** | Amarelo / verde | Clock I2C |
| **DIR** | **GND** | Roxo | Trava de direção (obrigatório para um ângulo estável) |

```
   E01 AS5600                         E02 Nano
   +-----------+                      +-----------+
   | VCC       |-------- vermelho ----| 5V        |
   | GND       |-------- preto -------| GND       |
   | SDA       |-------- azul --------| A4        |
   | SCL       |-------- amarelo -----| A5        |
   | DIR       |-------- roxo --------| GND       |
   |           |                      | USB  ---------- E06 ---------- computador
   +-----------+                      +-----------+
         ^
         |  chip virado para CIMA
```

Regras:

- Cada ponta de jumper precisa encaixar na **mesma fileira da protoboard** do pino a que deve se ligar (ou prender no header do módulo).
- Não ligue 5V em um módulo só de 3.3V.
- O GND precisa ser compartilhado. Sem GND → nada funciona.
- **DIR** e **GND** podem compartilhar a mesma fileira GND do Nano. Um jumper curto de DIR para GND no módulo é o mesmo eletricamente.

### Pinos extras não aumentam a resolução

O chip continua **12 bits** (4096 passos, cerca de **0,088°**). DIR→GND não acrescenta bits. Impede que um DIR flutuante inverta a direção da contagem ao acaso, que é o que parece “má precisão”.

| Pino extra | Ligar? | Por quê |
|-----------|----------|-----|
| **DIR** | **Sim → GND** | Datasheet: precisa ser um nível lógico de verdade. Flutuante = ângulo pulando. |
| **OUT** | Não para a captura | O analógico no Nano é 10 bits, pior que I2C. |
| **PGO** | Não — deixe aberto | Pino de programação. Ligar em GND pode colocar o chip em modo de gravação/programação. |
| **GPO** | Não | Não é usado para ângulo I2C. |

Com o DIR já amarrado, mais fios não deixam a agulha mais precisa. O que deixa:

- Um ímã diametral, **1–3 mm**, centralizado e paralelo sobre o chip
- **I2C** (`needle_angle_stream.ino`), não OUT analógico
- Jumpers curtos e 5V/GND sólidos (a maioria dos módulos já tem capacitor de desacoplamento)
- O firmware já configura histerese de 2 LSB e um filtro lento 16× no chip, lê o registrador ANGLE filtrado e faz a média de 8 amostras

### Posicione o ímã

Segure o disco **plano**, **1–3 mm** acima do **centro do chip preto** no E01. Paralelo à placa, como uma moeda flutuando.

```
        vista lateral

        E03   (=======)   disco diametral, plano
                   |
                   |  ~1-3 mm de ar
                   v
        E01   [#### chip ####]==== PCB ====
```

Você vai girá-lo **no lugar** depois que o firmware estiver rodando. Não pressione contra o chip. Não cole ainda.

Segure o ímã 1–3 mm acima do chip preto pequeno e então reconecte o USB.

### Grave o firmware

Você precisa do **Arduino IDE** neste passo. O arquivo é firmware Arduino, não um script Python. Usa só a biblioteca `Wire` nativa. **Não** instale uma biblioteca extra de AS5600. **Não** execute este arquivo com `python`.

Caminho:

```
firmware/needle_angle_stream/needle_angle_stream.ino
```

1. Abra o aplicativo **Arduino IDE** (pelo menu de aplicativos, ou `arduino` / `arduino-ide` no Linux). Isto não é um comando Python de terminal.
2. **File → Open**. No seletor de arquivos, entre neste projeto e então:

   `firmware` → `needle_angle_stream` → selecione **`needle_angle_stream.ino`** → **Open**

3. O editor precisa mostrar C++ que começa com `#include <Wire.h>`. Se aparecer Python, você abriu o arquivo errado.
4. **Tools → Board → Arduino AVR Boards → Arduino Nano**
5. **Tools → Processor → ATmega328P**
6. **Tools → Port** → a porta de [Instalação](#instalação):
   - Windows: `COMx` (por exemplo `COM3`)
   - macOS: `/dev/cu.usbserial-…`
   - Linux: `/dev/ttyUSB0` ou `/dev/ttyACM0`

   Sem porta, ou **Permission denied**? Volte à seção de instalação do seu sistema operacional. No Linux isso quase sempre é o grupo serial mais um logout completo.

7. Clique no botão **Upload** no topo (seta para a direita **→**).  
   Espere até a parte de baixo do IDE dizer **Done uploading**.

Se o Upload falhar:

1. **Tools → Processor → ATmega328P (Old Bootloader)** → faça Upload de novo.
2. Confirme a **Port** correta.
3. Feche o Serial Monitor e então grave (um monitor aberto pode travar a porta).
4. Pressione o botão **RESET** do Nano exatamente quando o upload começar.

Esse passo copia o nosso programa **para dentro do Nano**. Depois disso, sempre que o Nano tiver energia ele imprime `a=123.4` pelo USB.

### Veja a rotação no computador

Você pode acompanhar o fluxo no Arduino IDE ou no terminal. Qualquer um basta para comprovar a eletrônica.

**No Arduino IDE**

1. **Tools → Serial Monitor** (ou Ctrl+Shift+M / Cmd+Shift+M).
2. Embaixo à direita: baud **115200** (precisa coincidir com o sketch).
3. Você deve ver um fluxo:

```
a=47.3
a=47.4
a=48.1
a=90.2
```

**No terminal** (precisa de Python e pyserial de [Instalação](#instalação))

Na raiz do repositório:

```bash
python host/capture.py --all
# ou, se existirem várias portas:
# python host/capture.py --all --port COM3
# python host/capture.py --all --port /dev/ttyUSB0
```

Gire o ímã. Depois do upload você deve ver `filter: hyst=2lsb …` uma vez, e então linhas mudando:

```text
a=47.28    (gire o ímã — este número deve se mover)
a=90.14    (gire o ímã — este número deve se mover)
```

**Ctrl+C** interrompe o programa Python.

Uma volta completa deve cobrir cerca de 0–360 (ou dar a volta 359 → 0).

### Se aparecer `scan: none` — faça a checagem dos fios

O software nem sempre consegue nomear um único fio aberto (I2C precisa de VCC, GND, SDA e SCL). Este sketch extra testa os pinos do Nano, um jumper opcional **OUT→A0**, e imprime um veredito. Mantenha **DIR → GND** como na ligação normal de cinco fios.

1. Mantenha VCC, GND, SDA, SCL e **DIR → GND** como de costume.
2. Acrescente um jumper extra se o módulo tiver **OUT** (só para diagnóstico): **AS5600 OUT → Nano A0**.
3. Arduino IDE → **File → Open** → `firmware/wire_check/wire_check.ino` → **Upload**.
4. Olhe o Serial Monitor em **115200**, ou execute `python host/capture.py --all`.
5. Leia a linha **Verdict**.

| Veredito | Significado |
|---------|---------|
| I2C works | Use `needle_angle_stream.ino` de novo |
| A4/A5 sit LOW | Esse jumper está em curto ou na fileira errada |
| Module looks POWERED | VCC/GND OK → SDA ou SCL errado ou invertido |
| A0 near 0 | Sem energia no módulo, ou OUT não está em A0 |

Quando a checagem terminar, grave `needle_angle_stream.ino` de novo. Deixe o jumper OUT desconectado no uso normal.

### O que a saída significa

| Você vê | Significado | O que fazer |
|---------|---------|------------|
| `a=123.4` mudando quando você gira | Sucesso | Pare aqui; siga para o disco de madeira |
| `a=ERR` repetindo | Falha I2C: fiação ou energia | Recheque VCC/GND/SDA/SCL; 5V vs 3.3V |
| `a=` travado em um número | Ímã longe demais, fora do chip, ou axial (tipo errado) | Centralize um ímã diametral 1–3 mm sobre o CI |
| Serial Monitor em branco | Baud ou porta errados | 115200; a mesma porta do Upload |
| Erro de Upload | Bootloader ou porta | Tente Old Bootloader; feche o Monitor |
| Sem porta / permission denied | Driver (Windows/macOS) ou grupo serial (Linux) | Veja [Instalação](#instalação) |

**Critério de aprovação:** os ângulos mudam de forma suave quando você gira o E03.  
**Não monte o disco de madeira até isso passar.**

---

## Mecânica

Faça isto somente depois que a eletrônica passar.

Você precisa do rolamento, do eixo, da balsa, do círculo de madeira, da cola e do contrapeso de parafuso e porca. A eletrônica permanece como está; você só está acrescentando o conjunto que gira.

```
   Agulha M03
   ======●================►
         |
   M02   |  eixo através de M01
         |
   M04   =======( M01 )=======   base de madeira
         |
         v  PONTA do eixo
   E03   [=======]   ímã maciço colado na ponta
         |
         |  1-3 mm
         v
   E01   [ AS5600 ]  fixo sob a base
         |
   E02   [ Nano ] ---- USB ---- computador
```

1. Corte **M02** com cerca de 25–40 mm.
2. Encaixe **M01** no centro de **M04**. Passe **M02** por **M01** (eixo só pelo rolamento).
3. Cole **E03** com **M05a** na **ponta de baixo** de M02, centralizado. Folga até o chip do E01: **1–3 mm**.
4. Cole **M03** no **topo** de M02. Coloque **M05b** (parafuso e porca) na extremidade curta; acrescente ou tire porcas até a agulha ficar parada em qualquer ângulo.
5. Marque **A–Z** e depois **0–9** a cada **10°** em M04 (36 setores), ou imprima um dos discos em `docs/base-templates/`. A agulha não pode raspar a face.
6. Reconecte o USB e confirme que o ângulo ainda muda quando a agulha gira.

---

## Digitar letras

O Nano só envia `a=123.4`. O Python transforma isso em A–Z e 0–9.

Você precisa de:

- Firmware já gravado e transmitindo
- Python 3 e pyserial, de [Instalação](#instalação)
- Um disco de madeira pronto, ou pelo menos 36 marcas para apontar
- Para `--sound`, um sintetizador de voz da mesma seção de instalação

**Uma vez:** salve o ângulo real do sensor de cada letra impressa.  
**Todo dia:** confirme A, J, S, 1, e então digite.

### Primeira vez — `--calibrate`

```bash
python host/capture.py --calibrate
```

1. Aponte para **A**, toque espaço. Depois **B**, **C**, … **Z**, **0**–**9**, no **sentido horário**.
2. Espere o número ao vivo **mudar** antes de cada toque. Pare no traço, não depois dele.
3. Um toque que volte ou caia em uma letra anterior é recusado. Se dois toques caírem no mesmo ângulo, as duas letras anteriores são refeitas uma vez; se ainda estiverem próximas, aquela leitura é salva.
4. Os 36 ângulos são gravados em `host/config.json`. Nada é digitado neste modo.
   O `config.json` anterior é copiado antes para `host/config-backups/config_YYYY_MM_DD_HH_MM.json`.

Refaça `--calibrate` se remontar o ímã, reimprimir o disco, ou se as letras continuarem erradas depois de uma sessão normal.

Liste ou restaure um mapa antigo (não precisa do Nano):

```bash
python host/capture.py --restore              # listar backups
python host/capture.py --restore latest       # backup mais novo → config.json
python host/capture.py --restore config_2026_08_16_21_56.json
```

`--restore` copia o arquivo vivo atual para `config-backups/` antes de sobrescrevê-lo.

### Cada sessão — sem flags

```bash
python host/capture.py
# ou, fale cada letra digitada:
python host/capture.py --sound
```

Precisa de um `--calibrate` concluído antes.

1. Aponte para **A** (topo), toque. Depois **J** (direita), **S** (baixo), **1** (esquerda).
2. A linha ao vivo mostra `need 63°` (exemplo) — espere o número chegar perto daquela marca salva, então toque. J e S no mesmo ângulo são recusados.
3. A linha ao vivo diz **ready** / `tap space, then move`. Toque espaço. A captura **não** começa na letra em que você está (em geral **1**). Mova a agulha primeiro; o temporizador mostra `move` até você sair dessa letra. Depois mantenha uma letra cerca de **1 segundo** para digitar. Mantenha no **vão de 3,5°** entre as letras para digitar um espaço.

```text
 328.1° A    0.7s | HELLO
```

Ângulo, letra atual, temporizador de espera, texto até agora. Depois que uma letra é impressa o temporizador mostra `ok` — saia dela antes da próxima. Uma linha nova começa depois de 60 caracteres.

Logs: `logs/Session_YYYY_MM_DD_HH_MM.txt` (letras) e `.log` (letras + ângulos).

### Falar cada letra — `--sound`

Acrescente `--sound` ao comando diário de digitação. Depois que uma letra (ou espaço) é digitada, o computador a fala em voz alta. Os outros modos (`--calibrate`, `--debug`, `--all`, …) não falam.

```bash
python host/capture.py --sound
```

- As letras são faladas como estão (`A`, `B`, …).
- Os dígitos são palavras (`zero` … `nine`).
- Um vão digita um espaço e diz **space**.

Instale um pacote de fala **uma vez**, em [Instalação](#instalação). Se nenhum sintetizador for encontrado, a digitação continua funcionando e o programa imprime `No speech engine`.

### Se algo parecer errado

| Sintoma | Comando |
|---------|---------|
| Quero ver só o ângulo bruto | `--debug` |
| A letra está consistentemente errada | `--diagnostic` (depois envie o log) |
| O ímã / chip não enxerga uma volta completa | `--span` |
| Nenhum `a=…` | `--all` |

### Checagem de ponta a ponta

1. `python host/capture.py --calibrate` — todos os 36 traços, sentido horário.
2. `python host/capture.py` — A, J, S, 1 (espere `need …°`), depois **ready**. Toque espaço, **mova** a agulha, então mantenha uma letra.
3. A captura ignora a letra em que você estava (em geral **1**) até você se mover. Depois mantenha cerca de 1 s → **um** caractere. O temporizador mostra `move`, depois conta, depois `ok`.
4. Vá para a próxima letra e mantenha. Depois de 60 caracteres uma linha nova começa.

---

## Referência do capture.py

O firmware imprime `a=…`. O Python é `host/capture.py`. Um modo de cada vez.

```bash
python host/capture.py --help
```

No Windows, `python` é o comando usual. No macOS e em algumas instalações Linux é `python3`. Se você criou um ambiente virtual, ative-o primeiro.

### Modos

| Flag | Quando usar | O que acontece |
|------|-------------|--------------|
| *(nenhuma)* | Digitação diária | Carrega o mapa de 36 letras. Confirme **A, J, S, 1**. Toque **ready**, depois **mova** a agulha. Digita um caractere quando aquela letra permanece por `--delay` segundos. |
| `--calibrate` | Primeira vez, ou depois de mudança de hardware | Percorra **A–Z, 0–9**. Salve cada ângulo bruto em `host/config.json`. Copia o arquivo antigo para `host/config-backups/` primeiro. **Não** digita. |
| `--restore` | Desfazer uma calibração | Lista backups, ou copia um de volta sobre `config.json`. Não precisa de USB. `latest` = o mais novo. |
| `--debug` | Alinhar a base | Só o ângulo ao vivo. Gire a **base** (agulha em A) até A ficar onde você quer o norte. Ctrl+C para parar. |
| `--diagnostic` | Letras desalinhadas | Confirme A, J, S, 1. Aponte para uma letra impressa, **Enter**, digite essa letra, Enter. Repita. Ctrl+C grava um resumo em `logs/Diagnostic_*.log` (ângulo bruto, mapa salvo, letra prevista, erro). |
| `--all` | Checar o firmware | Imprime **toda** amostra bruta `a=…`. Sem letras. |
| `--stream` | O mesmo, mas mais silencioso | Imprime uma linha nova só quando o ângulo se move bastante (veja `--change-pct`). |
| `--span` | Checar o ímã | Registra mín/máx enquanto você dá uma volta completa. Span ≥ 300° é bom. |

### Serial (qualquer modo)

| Opção | Padrão | O que faz |
|--------|---------|--------------|
| `--port` | o primeiro `/dev/ttyUSB*`, `/dev/ttyACM*`, ou `cu.usbserial*` no macOS | Porta USB. Exemplo: `--port COM3` ou `--port /dev/ttyUSB0` |
| `--baud` | `115200` | Precisa coincidir com o sketch |

### Digitação (modo padrão)

| Opção | Padrão | O que faz |
|--------|---------|--------------|
| `--delay` | `1.0` (salvo como `delay_s`) | Segundos que a **mesma letra** precisa permanecer na tela antes de ser digitada. Jitter analógico está ok; mudar de letra zera o temporizador. |
| `--wrap` | `60` (salvo como `wrap_cols`) | Nova linha depois desta quantidade de caracteres. |
| `--invert` | desligado | Força a direção inversa. Normalmente vem de `--calibrate`. |
| `--sound` | desligado | Fala cada letra digitada ou “space” (somente digitação diária). Precisa de um sintetizador de voz de [Instalação](#instalação). Offline, sem conta. |
| `--log-dir` | `logs/` na raiz do repositório | Para onde vão `Session_*.txt` / `Session_*.log`. |

`--delay` e `--wrap` ficam gravados em `host/config.json`. Eles **não** sobrescrevem as 36 marcas das letras.

`--still-tol` e `--move-deg` são flags restantes. A digitação não as usa.

### Stream / span

| Opção | Padrão | O que faz |
|--------|---------|--------------|
| `--change-pct` | `10` (36°) | Com `--stream`, nova linha quando o ângulo se move esta porcentagem de uma volta. Mudar isso de 10 também entra no modo stream. |
| `--span-seconds` | `12` | Por quanto tempo `--span` registra. |

### O que é salvo (`host/config.json`)

`--calibrate` grava as 36 letras mais `invert`. Uma sessão normal só **lê** esse arquivo e mede A, J, S, 1 para alinhar o mapa com a posição da base de hoje.

```text
--calibrate     salvar A=327.4, B=338.1, … 9=317.4
python capture  medir A, J, S, 1
                esticar as 36 marcas salvas sobre esses quatro
                letra = marca salva mais próxima, ou espaço nos vãos de 3,5°
                digitar quando essa letra (ou espaço) permanecer ~1 s
```

### Exemplos

```bash
python host/capture.py --help
python host/capture.py --calibrate       # uma vez: salvar as 36 letras (faz backup do arquivo antigo)
python host/capture.py --restore         # listar mapas salvos
python host/capture.py --restore latest  # colocar o mapa anterior de volta
python host/capture.py                   # diário: A J S 1, depois digitar
python host/capture.py --debug           # ângulo bruto; gire a base
python host/capture.py --diagnostic      # Enter + letra verdadeira → Diagnostic_*.log
python host/capture.py --sound           # falar cada letra
python host/capture.py --delay 1.5       # manter um pouco mais antes de digitar
python host/capture.py --wrap 40
python host/capture.py --port COM3
python host/capture.py --all             # cada a=… bruto
python host/capture.py --span            # mín/máx de uma volta completa
```

---

## Folha de consulta do Arduino

O `.ino` é aberto e gravado **somente** no Arduino IDE. O Python é só `host/capture.py`.

| Tarefa | Onde |
|------|--------|
| Abrir o firmware | Arduino IDE → File → Open → `firmware/needle_angle_stream/needle_angle_stream.ino` |
| Escolher a placa | Tools → Board → Arduino AVR Boards → Arduino Nano |
| Escolher o chip / bootloader | Tools → Processor → ATmega328P (ou Old Bootloader) |
| Escolher a porta USB | Tools → Port |
| Enviar o programa para o Nano | Botão Upload (→) |
| Ver `a=…` no IDE | Tools → Serial Monitor, baud **115200** |
| Ver `a=…` no terminal | `python host/capture.py --all` **depois** que o Upload der certo |

O endereço I2C do AS5600 é `0x36`. O ângulo é 12 bits (0–4095) → graus = `raw * 360 / 4096`. O firmware grava CONF (histerese 2 LSB, filtro lento 16×), lê o registrador ANGLE filtrado (`0x0E`) e imprime a média de 8 amostras como `a=123.45`.

---

## Pronto quando

- [ ] A porta do Nano aparece; o firmware é gravado
- [ ] O Serial Monitor ou `capture.py --all` mostra `a=…` mudando quando o ímã ou a agulha gira
- [ ] A agulha está livre e equilibrada
- [ ] O anel das letras está em M04
- [ ] Manter uma letra imprime um caractere
- [ ] Um arquivo de log cresce em `logs/`

### Reproduzir do zero

1. Compre a lista de itens; solde os headers do Nano se precisar; pegue um cabo Mini-B **de dados** se o kit não tiver.
2. Instale Arduino IDE, Python e pyserial para o seu sistema operacional. Confirme a porta USB.
3. Cinco fios (incluindo DIR→GND), ímã sobre o chip, grave o sketch, acompanhe a serial em 115200.
4. Rolamento, eixo, ímã na **ponta**, agulha, letras.
5. `python host/capture.py --calibrate` uma vez, depois `python host/capture.py` em cada sessão (A, J, S, 1, manter para digitar).
