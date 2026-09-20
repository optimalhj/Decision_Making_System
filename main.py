import torch
from torch import nn
from torch.nn import functional as F
from torch import optim

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

def drawing(pmf, bin_edges):
    plt.figure(figsize=(8, 5))

    # 각 구간의 중간값(Center)을 구해 막대의 x축 위치로 사용합니다.
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

    # 막대그래프 그리기 (각 구간의 폭은 100에서 약간의 여백을 둠)
    plt.bar(bin_centers, pmf, width=bin_edges[1:] - bin_edges[:-1], color='skyblue', edgecolor='black', alpha=0.8)

    # 그래프 서식 설정
    plt.title('Probability Mass Function (PMF)', fontsize=14, fontweight='bold', pad=15)
    plt.xlabel('Data Intervals (Bins)', fontsize=12)
    plt.ylabel('Probability', fontsize=12)

    # X축 눈금을 구간의 경계값(bin_edges)으로 명확하게 표시
    plt.xticks(bin_edges)
    plt.grid(axis='y', linestyle='--', alpha=0.5)

    # 각 막대 위에 확률 값 텍스트 표시
    for i in range(len(pmf)):
        plt.text(bin_centers[i], pmf[i] + 0.01, f'{pmf[i]:.2f}', ha='center', va='bottom', fontweight='bold')

    # Y축 범위에 약간의 여유를 주어 텍스트가 잘리지 않게 함
    plt.ylim(0, max(pmf) + 0.05)

    plt.tight_layout()
    plt.show()

class Qnet(nn.Module):
    def __init__(self):
        super(Qnet, self).__init__()
        self.layer1 = nn.Linear(6, 64)
        self.layer2 = nn.Linear(64, 128)
        self.layer3 = nn.Linear(128, 64)
        self.layer4 = nn.Linear(64, 32)
        self.layer5 = nn.Linear(32, 1)
    def forward(self, x):
        x = self.layer1(x)
        x = F.relu(x)
        x = self.layer2(x)
        x = F.relu(x)
        x = self.layer3(x)
        x = F.relu(x)
        x = self.layer4(x)
        x = F.relu(x)
        x = self.layer5(x)
        return x

def build_list_state(demand_qty, rates, t, scaling):
    now_demand = demand_qty[t]
    today_rate = rates[t - 1]

    demand_until_now = np.array(demand_qty[:t + 1])
    mean_demand = np.mean(demand_until_now)
    std_demand = np.std(demand_until_now, ddof=1) if demand_until_now.size > 1 else 0

    rate_until_now = np.array(rates[:t])
    mean_rate = np.mean(rate_until_now)
    std_rate = np.std(rate_until_now, ddof=1) if rate_until_now.size > 1 else 0
    list_state = [now_demand / scaling, today_rate, mean_demand/scaling, std_demand/scaling, mean_rate, std_rate]
    return list_state

def train(demand_qty, rates, scaling, device, every_episode=2000, cycle=4, lr=0.001):
    qnet = Qnet().to(device)

    optimizer = optim.RMSprop(qnet.parameters(), lr=lr)

    list_states, real_rates = [], []
    for epoch in range(every_episode):

        for t in range(1, len(demand_qty)):
            list_state, next_rate = build_list_state(demand_qty, rates, t, scaling), [rates[t]]
            list_states.append(list_state)
            real_rates.append(next_rate)

        if (epoch + 1) % cycle == 0:
            torch_states = torch.Tensor(list_states).to(device)
            pred_rate = qnet(torch_states)

            real_rates = torch.Tensor(real_rates).to(device)

            loss = F.l1_loss(pred_rate, real_rates)
            print("Loss :", loss.item())
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            list_states, real_rates = [], []
    return qnet.state_dict()

def test(demand_qty, rates, scaling, parameters, device):

    qnet = Qnet().to(device)
    qnet.load_state_dict(parameters)
    qnet.eval()

    list_state = build_list_state(demand_qty, rates, len(demand_qty) - 1, scaling)
    print(list_state)
    torch_state = torch.Tensor([list_state]).to(device)
    with torch.no_grad():
        pred_rate = qnet(torch_state)
    print(pred_rate.item() / 100)

def main():

    data_name = 'demand_data_p1'
    df = pd.read_excel(data_name+'.xls')
    demand_qty = df.DEMAND_QTY.values.tolist()
    demand_qty.reverse()

    today_indices = 1800
    next_day_demand = demand_qty[today_indices]
    demand_qty=demand_qty[:min(today_indices, len(demand_qty))]
    rates = [((demand_qty[t + 1] / demand_qty[t]) - 1) * 100 for t in range(len(demand_qty) - 1)]
    print(len(demand_qty), demand_qty)
    print(len(rates), rates)
    today_demand = demand_qty.pop(len(demand_qty) - 1)

    scaling = 1000

    learning = False
    if learning or not Path(data_name+".pt").is_file():
        device = torch.device("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
        print("We are using", device)
        torch.save(train(demand_qty, rates, scaling=scaling, device=device), data_name+'.pt')
    else:
        device = torch.device("cpu")
        print("We are using", device)
        print("Next  =", next_day_demand)
        print("Today =", today_demand)
        print("Real Rate :", next_day_demand / today_demand - 1)
        test(demand_qty+[today_demand], rates, scaling, torch.load(data_name + ".pt"), device)


if __name__ == "__main__":
    main()