# 飞机对海打击

用于航空兵攻击蓝方已探测的水面舰艇。Agent 选择具备适配挂载的蓝方飞机，并从 `contacts.sea` 选择目标；固定脚本完成任务创建、目标绑定和单元分配。

必填：`mission_name`、`unit_guids`、`target_contact_guids`。没有水面接触时不能创建。反舰挂载 GUID 只有确定时才填写。
