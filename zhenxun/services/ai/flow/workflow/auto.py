import graphlib
from typing import Any

from zhenxun.services.ai.flow.workflow.engine import Workflow
from zhenxun.services.ai.flow.workflow.nodes import NodeFactory, Parallel, Router
from zhenxun.services.log import logger


class AutoWorkflow(Workflow):
    """
    自动化声明式工作流 (Facade)。
    允许开发者通过 @entry, @listen 装饰器定义类方法，
    在初始化时，底层编译器会自动分析依赖并推导为原生的 Steps 和 Parallel 节点图。
    """

    def __init__(self, name: str | None = None, description: str = "", **kwargs: Any):
        workflow_name = name or self.__class__.__name__
        compiled_steps = self._compile_graph()

        super().__init__(
            name=workflow_name, steps=compiled_steps, description=description
        )

        self._auto_kwargs = kwargs

    def _compile_graph(self) -> list[Any]:
        """核心图推导编译器：支持 Router 嵌套与 AND/OR 拓扑排序"""
        methods_meta = {}
        router_paths = set()

        for attr_name in dir(self):
            if attr_name.startswith("_"):
                continue
            attr = getattr(self, attr_name)
            if hasattr(attr, "__workflow_meta__"):
                methods_meta[attr_name] = attr.__workflow_meta__
                if attr.__workflow_meta__.paths:
                    router_paths.update(attr.__workflow_meta__.paths)

        if not methods_meta:
            logger.warning(
                f"AutoWorkflow '{self.__class__.__name__}' 没有检测到任何被装饰的方法！"
            )
            return []

        top_level_methods = {
            k: v
            for k, v in methods_meta.items()
            if not any(t in router_paths for t in v.triggers)
        }
        branch_methods = {
            k: v
            for k, v in methods_meta.items()
            if any(t in router_paths for t in v.triggers)
        }

        ts = graphlib.TopologicalSorter()
        for name, meta in top_level_methods.items():
            valid_triggers = [t for t in meta.triggers if t in top_level_methods]
            ts.add(name, *valid_triggers)

        try:
            ts.prepare()
        except graphlib.CycleError as e:
            raise ValueError(f"AutoWorkflow 编译失败：检测到循环依赖！{e}")

        workflow_steps = []
        while ts.is_active():
            ready_nodes = ts.get_ready()
            step_nodes = []
            for n in ready_nodes:
                meta = top_level_methods[n]
                if meta.type in ("router", "entry_router"):
                    choices = []
                    for path in meta.paths:
                        branch_name = next(
                            (
                                bk
                                for bk, bv in branch_methods.items()
                                if path in bv.triggers
                            ),
                            None,
                        )
                        if branch_name:
                            choices.append(
                                NodeFactory.build(getattr(self, branch_name), name=path)
                            )

                    step_nodes.append(
                        Router(name=n, selector=getattr(self, n), choices=choices)
                    )
                else:
                    step_nodes.append(NodeFactory.build(getattr(self, n), name=n))

            if len(step_nodes) == 1:
                workflow_steps.append(step_nodes[0])
            elif len(step_nodes) > 1:
                workflow_steps.append(
                    Parallel(*step_nodes, name=f"Parallel_{'_'.join(ready_nodes)[:30]}")
                )

            for node in ready_nodes:
                ts.done(node)

        return workflow_steps
