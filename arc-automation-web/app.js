const ARC_CHAIN_ID = 5042002n;
const EXPLORER = "https://testnet.arcscan.app/tx/";
const USDC = "0x3600000000000000000000000000000000000000";
const EURC = "0x89B50855Aa3bE2F677cD6303Cec089B5F319D72a";
const CURVE_POOL = "0x2D84D79C852f6842AbE0304b70bBaA1506AdD457";

const ERC20_ABI = [
  "function balanceOf(address) view returns (uint256)",
  "function decimals() view returns (uint8)",
  "function symbol() view returns (string)",
  "function allowance(address,address) view returns (uint256)",
  "function approve(address,uint256) returns (bool)",
  "function transfer(address,uint256) returns (bool)"
];

let provider;
let signer;
let signerAddress = "";
let activityStopRequested = false;
const runLog = [];

const $ = (id) => document.getElementById(id);
const rpcUrl = () => $("rpcUrl").value.trim() || "https://rpc.testnet.arc.network";
const log = (line) => {
  const stamped = `[${new Date().toISOString()}] ${line}`;
  runLog.push(stamped);
  $("log").textContent = runLog.join("\n");
};

function getProvider() {
  provider = new ethers.JsonRpcProvider(rpcUrl(), { chainId: Number(ARC_CHAIN_ID), name: "arc-testnet" });
  return provider;
}

async function setSigner(nextSigner, label) {
  signer = nextSigner;
  signerAddress = await signer.getAddress();
  $("signerStatus").textContent = `${label}: ${signerAddress}`;
  await refreshBalances();
}

async function requireSigner() {
  if (!signer) throw new Error("No signer connected");
  const net = await signer.provider.getNetwork();
  if (net.chainId !== ARC_CHAIN_ID) throw new Error(`Wrong chain ${net.chainId}; expected ${ARC_CHAIN_ID}`);
  return signer;
}

async function sendTx(taskName, contract, fn, args = [], overrides = {}) {
  const s = await requireSigner();
  const connected = contract.connect(s);
  log(`${taskName}: simulating ${fn}...`);
  try {
    await connected[fn].staticCall(...args, overrides);
  } catch (err) {
    const message = err.shortMessage || err.message || String(err);
    log(`${taskName}: simulation warning: ${message}`);
    if (!confirm(`${taskName} simulation did not return cleanly. Continue sending anyway?\n\n${message}`)) {
      throw new Error(`${taskName}: cancelled after simulation warning`);
    }
  }
  const tx = await connected[fn](...args, overrides);
  log(`${taskName}: sent ${tx.hash}`);
  const receipt = await tx.wait();
  if (receipt.status !== 1) throw new Error(`${taskName}: transaction failed ${tx.hash}`);
  log(`${taskName}: confirmed ${EXPLORER}${tx.hash}`);
  return tx.hash;
}

async function approveExactIfNeeded(spender, amount) {
  const s = await requireSigner();
  const usdc = new ethers.Contract(USDC, ERC20_ABI, s);
  const allowance = await usdc.allowance(signerAddress, spender);
  if (allowance >= amount) return null;
  log(`approve: exact USDC allowance ${ethers.formatUnits(amount, 6)} to ${spender}`);
  const tx = await usdc.approve(spender, amount);
  const receipt = await tx.wait();
  if (receipt.status !== 1) throw new Error(`approve failed ${tx.hash}`);
  log(`approve: confirmed ${EXPLORER}${tx.hash}`);
  return tx.hash;
}

async function approveTokenExactIfNeeded(tokenAddress, spender, amount, decimals = 6) {
  const s = await requireSigner();
  const token = new ethers.Contract(tokenAddress, ERC20_ABI, s);
  const allowance = await token.allowance(signerAddress, spender);
  if (allowance >= amount) return null;
  log(`approve: exact allowance ${ethers.formatUnits(amount, decimals)} to ${spender}`);
  const tx = await token.approve(spender, amount);
  const receipt = await tx.wait();
  if (receipt.status !== 1) throw new Error(`approve failed ${tx.hash}`);
  log(`approve: confirmed ${EXPLORER}${tx.hash}`);
  return tx.hash;
}

async function readNativeBalance() {
  const s = await requireSigner();
  return s.provider.getBalance(signerAddress);
}

function parsePositiveNumber(id, fallback) {
  const value = Number($(id).value);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function todayKey() {
  return new Date().toISOString().slice(0, 10);
}

function dailyStorageKey(taskId) {
  return `arc-automation-daily:${signerAddress.toLowerCase()}:${taskId}:${todayKey()}`;
}

function isDailyDone(taskId) {
  return Boolean(signerAddress && localStorage.getItem(dailyStorageKey(taskId)));
}

function markDailyDone(taskId, txHash) {
  localStorage.setItem(dailyStorageKey(taskId), JSON.stringify({ txHash, at: new Date().toISOString() }));
}

async function runTask(task) {
  if (task.frequency === "daily" && isDailyDone(task.id)) {
    log(`SKIP ${task.title}: already completed locally today for ${signerAddress}`);
    return null;
  }
  const result = await task.run();
  if (task.frequency === "daily") {
    markDailyDone(task.id, result || "ok");
  }
  return result;
}

const tasks = [
  {
    id: "balance",
    title: "Balance check",
    frequency: "utility",
    desc: "Reads native USDC, ERC-20 USDC, and EURC balances.",
    run: refreshBalances
  },
  {
    id: "arcGm",
    title: "Arc GM Portal",
    frequency: "daily",
    desc: "Daily 1x/day: calls sayGM() on the verified Arc GM portal contract.",
    run: async () => sendTx("Arc GM Portal", new ethers.Contract("0x99eD064801Efbb050edFd99a1DFB57fe12A25C92", ["function sayGM()"], getProvider()), "sayGM")
  },
  {
    id: "zkGm",
    title: "zkCodex GM",
    frequency: "daily",
    desc: "Daily 1x/day: calls sayGM() on zkCodex Arc GM contract.",
    run: async () => sendTx("zkCodex GM", new ethers.Contract("0x1290B4f2a419A316467b580a088453a233e9ADCc", ["function sayGM()"], getProvider()), "sayGM")
  },
  {
    id: "onChainGm",
    title: "OnChainGM",
    frequency: "daily",
    desc: "Daily 1x/day: calls onChainGM() with the known 0.5 native USDC fee.",
    run: async () => sendTx("OnChainGM", new ethers.Contract("0x363cC75a89aE5673b427a1Fa98AFc48FfDE7Ba43", ["function onChainGM() payable"], getProvider()), "onChainGM", [], { value: ethers.parseEther("0.5") })
  },
  {
    id: "zkCounter",
    title: "zkCodex Counter",
    frequency: "loopable",
    desc: "Loopable: increments zkCodex counter with exact 0.01 native USDC fee.",
    run: async () => sendTx("zkCodex Counter", new ethers.Contract("0xfcF1E3e7890559c56013457e7073791ed27060a1", ["function incrementCounter() payable"], getProvider()), "incrementCounter", [], { value: ethers.parseEther("0.01") })
  },
  {
    id: "zkScore",
    title: "zkCodex ScoreMint",
    frequency: "loopable",
    desc: "Loopable update: sets/updates a score with exact native USDC fee.",
    run: async () => sendTx("zkCodex ScoreMint", new ethers.Contract("0x705dB56640869439bF813b856a0fa944c6e2e8C4", ["function setScore(uint32,string) payable"], getProvider()), "setScore", [1, `arc-auto-${Date.now()}`], { value: ethers.parseEther("0.1") })
  },
  {
    id: "arcWorldKey",
    title: "ArcWorld Key NFT",
    frequency: "one-time",
    desc: "One-time/contract-limited: mints one ArcWorld Key NFT.",
    run: async () => sendTx("ArcWorld Key", new ethers.Contract("0x8621c6775335Ac1511f1787093153801DEf834C5", ["function mint(uint256)"], getProvider()), "mint", [1])
  },
  {
    id: "arcWorldPassport",
    title: "ArcWorld Passport",
    frequency: "one-time",
    desc: "Approves exactly 1 USDC, then creates an on-chain passport record.",
    run: async () => {
      const service = "0xb6496BD90611402B53B69cA48Cd956DbcA8BD57e";
      await approveExactIfNeeded(service, 1_000_000n);
      const dataHash = ethers.keccak256(ethers.toUtf8Bytes(JSON.stringify({ wallet: signerAddress, t: Date.now() })));
      return sendTx("ArcWorld Passport", new ethers.Contract(service, ["function createPassport(string,string,string,bytes32) returns (uint256)"], getProvider()), "createPassport", ["ARC CITIZEN", "ARC", "2000-01-01", dataHash]);
    }
  },
  {
    id: "arcWorldTransferSelf",
    title: "ArcWorld quick transfer self",
    frequency: "loopable",
    desc: "Loopable: sends USDC to the same wallet to exercise transfer activity.",
    run: async () => sendTx("USDC transfer self", new ethers.Contract(USDC, ERC20_ABI, getProvider()), "transfer", [signerAddress, 1000n])
  },
  {
    id: "surfGm",
    title: "SurfLayer Daily GM",
    frequency: "daily",
    desc: "Daily 1x/day: reads gasFee() then calls dailyGM() with exact value.",
    run: async () => {
      const c = new ethers.Contract("0xfceABEed1942559aF3080146A0B17758bEb28655", ["function gasFee() view returns (uint256)", "function dailyGM() payable"], getProvider());
      const fee = await c.gasFee();
      return sendTx("SurfLayer GM", c, "dailyGM", [], { value: fee });
    }
  },
  {
    id: "surfDeploy",
    title: "SurfLayer Deploy",
    frequency: "optional",
    desc: "Optional deploy-style action: reads gasFee() then calls deploy() with exact value.",
    run: async () => {
      const c = new ethers.Contract("0x50d9083c57216A64F74fA6f25D5a8a6bFFaCCe67", ["function gasFee() view returns (uint256)", "function deploy() payable"], getProvider());
      const fee = await c.gasFee();
      return sendTx("SurfLayer Deploy", c, "deploy", [], { value: fee });
    }
  }
];

function makeTransferActivity() {
  return async () => {
    const amount = ethers.parseUnits(parsePositiveNumber("activityTransferAmount", 0.001).toString(), 6);
    await sendTx("Activity transfer self", new ethers.Contract(USDC, ERC20_ABI, getProvider()), "transfer", [signerAddress, amount]);
  };
}

function makeSwapActivity() {
  let direction = 0;
  const poolAbi = [
    "function exchange(int128,int128,uint256,uint256) returns (uint256)",
    "function get_dy(int128,int128,uint256) view returns (uint256)"
  ];
  return async () => {
    const amount = ethers.parseUnits(parsePositiveNumber("activitySwapAmount", 0.001).toString(), 6);
    const source = direction % 2 === 0 ? USDC : EURC;
    const i = direction % 2 === 0 ? 0 : 1;
    const j = direction % 2 === 0 ? 1 : 0;
    direction += 1;
    await approveTokenExactIfNeeded(source, CURVE_POOL, amount, 6);
    const pool = new ethers.Contract(CURVE_POOL, poolAbi, getProvider());
    const quoted = await pool.get_dy(i, j, amount);
    const minOut = quoted * 90n / 100n;
    await sendTx(`Activity Curve swap ${i}->${j}`, pool, "exchange", [i, j, amount, minOut]);
  };
}

function makeMintActivity() {
  let score = 2;
  return async () => {
    const fee = ethers.parseEther(parsePositiveNumber("activityMintFee", 0.1).toString());
    const nextScore = score;
    score = score >= 100 ? 1 : score + 1;
    await sendTx(
      "Activity ScoreMint",
      new ethers.Contract("0x705dB56640869439bF813b856a0fa944c6e2e8C4", ["function setScore(uint32,string) payable"], getProvider()),
      "setScore",
      [nextScore, `activity-${Date.now()}-${nextScore}`],
      { value: fee }
    );
  };
}

function buildActivityActions() {
  const actions = [];
  if ($("loopTransfer").checked) actions.push({ label: "transfer", run: makeTransferActivity() });
  if ($("loopSwap").checked) actions.push({ label: "swap", run: makeSwapActivity() });
  if ($("loopMint").checked) actions.push({ label: "mint", run: makeMintActivity() });
  return actions;
}

async function runActivityMode() {
  await requireSigner();
  const actions = buildActivityActions();
  if (!actions.length) return log("Activity Mode: no action type selected.");
  const maxTx = Math.min(100, Math.floor(parsePositiveNumber("activityMaxTx", 10)));
  const delayMs = Math.max(10000, Math.floor(parsePositiveNumber("activityDelay", 30) * 1000));
  const budget = ethers.parseEther(parsePositiveNumber("activityBudget", 2).toString());
  const startBalance = await readNativeBalance();
  activityStopRequested = false;
  $("startActivity").disabled = true;
  $("stopActivity").disabled = false;
  $("activityStatus").textContent = `Running up to ${maxTx} tx...`;
  log(`Activity Mode: start maxTx=${maxTx}, delay=${delayMs / 1000}s, budget=${ethers.formatEther(budget)} native USDC`);

  try {
    for (let txCount = 0; txCount < maxTx; txCount += 1) {
      if (activityStopRequested) {
        log("Activity Mode: stopped by user.");
        break;
      }
      const spent = startBalance - await readNativeBalance();
      if (spent >= budget) {
        log(`Activity Mode: budget reached (${ethers.formatEther(spent)} native USDC).`);
        break;
      }
      const action = actions[txCount % actions.length];
      $("activityStatus").textContent = `Tx ${txCount + 1}/${maxTx}: ${action.label}`;
      log(`Activity Mode: tx ${txCount + 1}/${maxTx} ${action.label}`);
      try {
        await action.run();
      } catch (err) {
        log(`Activity Mode stopped on ${action.label}: ${err.shortMessage || err.message || err}`);
        break;
      }
      if (txCount < maxTx - 1) await sleep(delayMs);
    }
  } finally {
    $("activityStatus").textContent = "Idle.";
    $("startActivity").disabled = false;
    $("stopActivity").disabled = true;
    await refreshBalances().catch((err) => log(`Balance error: ${err.message}`));
  }
}

async function refreshBalances() {
  const p = getProvider();
  const address = signerAddress || (signer ? await signer.getAddress() : "");
  if (!address) {
    $("balances").textContent = "Connect signer first.";
    return;
  }
  const usdc = new ethers.Contract(USDC, ERC20_ABI, p);
  const eurc = new ethers.Contract(EURC, ERC20_ABI, p);
  const native = await p.getBalance(address);
  const usdcBal = await usdc.balanceOf(address);
  const eurcBal = await eurc.balanceOf(address);
  $("balances").textContent = [
    `Address: ${address}`,
    `Native USDC: ${ethers.formatEther(native)}`,
    `USDC ERC-20: ${ethers.formatUnits(usdcBal, 6)}`,
    `EURC: ${ethers.formatUnits(eurcBal, 6)}`
  ].join("\n");
}

function renderTasks() {
  $("taskList").innerHTML = tasks.map((task) => `
    <div class="task">
      <label>
        <input type="checkbox" data-task="${task.id}" />
        <strong>${task.title}</strong>
      </label>
      <small>${task.desc}</small>
      <span class="badge ${task.frequency}">${task.frequency}</span>
      ${task.frequency === "daily" && isDailyDone(task.id) ? '<span class="badge done">done today</span>' : ''}
    </div>
  `).join("");
}

async function runSelected() {
  const ids = [...document.querySelectorAll("[data-task]:checked")].map((el) => el.dataset.task);
  if (!ids.length) return log("No tasks selected.");
  $("runSelected").disabled = true;
  try {
    for (const id of ids) {
      const task = tasks.find((item) => item.id === id);
      try {
        log(`START ${task.title}`);
        await runTask(task);
        log(`DONE ${task.title}`);
      } catch (err) {
        log(`FAILED ${task.title}: ${err.shortMessage || err.message || err}`);
      }
    }
    await refreshBalances();
  } finally {
    $("runSelected").disabled = false;
  }
}

function exportLog() {
  const blob = new Blob([runLog.join("\n")], { type: "text/plain" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `arc-automation-log-${Date.now()}.txt`;
  a.click();
  URL.revokeObjectURL(a.href);
}

$("usePrivateKey").addEventListener("click", async () => {
  try {
    const key = $("privateKey").value.trim();
    if (!/^0x[0-9a-fA-F]{64}$/.test(key)) throw new Error("Invalid private key format");
    await setSigner(new ethers.Wallet(key, getProvider()), "Burner signer");
    $("privateKey").value = "";
    log("Burner key loaded into memory.");
  } catch (err) {
    log(`Signer error: ${err.message}`);
  }
});

$("connectInjected").addEventListener("click", async () => {
  try {
    if (!window.ethereum) throw new Error("No injected wallet found");
    const browserProvider = new ethers.BrowserProvider(window.ethereum);
    await browserProvider.send("eth_requestAccounts", []);
    await setSigner(await browserProvider.getSigner(), "Injected wallet");
    log("Injected wallet connected.");
  } catch (err) {
    log(`Wallet error: ${err.message}`);
  }
});

$("forgetSigner").addEventListener("click", () => {
  signer = null;
  signerAddress = "";
  $("signerStatus").textContent = "No signer connected.";
  $("balances").textContent = "Not loaded.";
  log("Signer forgotten.");
});

$("refreshBalances").addEventListener("click", () => refreshBalances().catch((err) => log(`Balance error: ${err.message}`)));
$("exportLog").addEventListener("click", exportLog);
$("runSelected").addEventListener("click", runSelected);
$("startActivity").addEventListener("click", () => runActivityMode().catch((err) => log(`Activity Mode error: ${err.message}`)));
$("stopActivity").addEventListener("click", () => {
  activityStopRequested = true;
  $("activityStatus").textContent = "Stopping after current transaction...";
});
$("selectSafe").addEventListener("click", () => {
  document.querySelectorAll("[data-task]").forEach((el) => {
    const task = tasks.find((item) => item.id === el.dataset.task);
    el.checked = Boolean(task && ["daily", "loopable", "utility"].includes(task.frequency) && !(task.frequency === "daily" && isDailyDone(task.id)));
  });
});

renderTasks();
log("Arc automation app ready.");
